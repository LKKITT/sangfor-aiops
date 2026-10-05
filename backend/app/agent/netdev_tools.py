"""网络设备（华为/H3C/锐捷）AI 对话工具集：查询、健康、路由、接口、ARP、日志、终端定位与配置下发。

设计要点：
- 仅面向 AI 对话场景，厂家白名单 AI_VENDORS（华为/H3C/锐捷）；其他厂家（思科/中兴等）
  继续走「网络设备管理」页的批量执行/控制台，工具返回友好指引；
- 工具自持设备解析：devices 参数（名称/管理IP/ID，可多个=批量）→ db 网络设备记录；
  当前会话绑定网络设备时缺省对绑定设备执行；needs_device=False（无 REST 客户端，SSH 直连）；
- 复用 netdev_service.run_commands（连接池/分页关闭/提权/脱敏）与 netdev_topology_service
  （ARP/MAC 缓存 + search_asset 终端定位）；
- 写操作（配置下发/任意命令）走统一确认卡片流程，配置默认不保存（可显式要求 save）。
"""
import re

from app import db
from app.agent.tools import Tool
from app.services import netdev_service
from app.services.device_scope import device_kind

# AI 对话支持的网络设备厂家（用户需求口径：只针对 H3C、华为、锐捷）
AI_VENDORS = {"huawei", "h3c", "ruijie"}
VENDOR_CN = {"huawei": "华为", "h3c": "H3C", "ruijie": "锐捷"}

# 每设备输出截断上限（多台批量时按台数缩放，总回传由编排器 TOOL_RESULT_LIMIT 兜底）
OUTPUT_CAP_PER_DEVICE = 6000

# 命令目录（按厂家）
HEALTH_CMDS = {
    "huawei": ["display version", "display cpu-usage", "display memory-usage", "display environment"],
    "h3c": ["display version", "display cpu-usage", "display memory", "display environment"],
    "ruijie": ["show version", "show cpu", "show memory"],
}
CONFIG_CMD = {"huawei": "display current-configuration",
              "h3c": "display current-configuration",
              "ruijie": "show running-config"}
IF_BRIEF_CMDS = {
    "huawei": ["display interface brief", "display ip interface brief"],
    "h3c": ["display interface brief", "display ip interface brief"],
    "ruijie": ["show interfaces status", "show ip interface brief"],
}
IF_DETAIL_CMD = {"huawei": "display interface {name}", "h3c": "display interface {name}",
                 "ruijie": "show interfaces {name}"}
ROUTE_CMD = {"huawei": "display ip routing-table", "h3c": "display ip routing-table",
             "ruijie": "show ip route"}
ROUTE_PROTO_CMD = {"huawei": "display ip routing-table protocol {proto}",
                   "h3c": "display ip routing-table protocol {proto}",
                   "ruijie": "show ip route {proto}"}
ARP_CMD = {"huawei": "display arp", "h3c": "display arp", "ruijie": "show arp"}
# 接口视图配置查看（dis this 模式）：进入接口视图看"当前视图生效配置"，再返回用户视图。
# 进入视图/return 均为查看动作，不修改任何配置
IF_VIEW_CONFIG = {
    # 注意：H3C/华为的 interface 命令必须先 system-view（用户视图下不可用）；
    # 序列同时取接口详情（状态/计数）与视图内 display this（生效配置），一次拿全
    "huawei": ["display interface {name}", "system-view", "interface {name}",
               "display this", "return"],
    "h3c": ["display interface {name}", "system-view", "interface {name}",
            "display this", "return"],
    "ruijie": ["show interfaces {name}", "show running-config interface {name}"],
}
# 接口名缩写 → 厂商全名（端口编号部分原样保留，大小写不敏感匹配前缀）
IFNAME_EXPANSIONS = [
    ("hge", "HundredGigE"), ("twe", "Twenty-Five-GigE"), ("fge", "FortyGigE"),
    ("xge", "Ten-GigabitEthernet"), ("te", "Ten-GigabitEthernet"),
    ("ge", "GigabitEthernet"), ("gi", "GigabitEthernet"),
    ("bagg", "Bridge-Aggregation"), ("ragg", "Route-Aggregation"),
    ("vlan-int", "Vlan-interface"), ("ve", "Vlan-interface"),
]

def expand_ifname(name: str) -> str:
    """接口缩写展开：GE1/0/21 → GigabitEthernet1/0/21 等；已是全名/无法识别则原样返回。"""
    s = str(name or "").strip()
    low = s.lower()
    for abbr, full in IFNAME_EXPANSIONS:
        if low.startswith(abbr) and len(s) > len(abbr) and s[len(abbr)].isdigit():
            return full + s[len(abbr):]
    return s
LOG_CMD = {"huawei": "display logbuffer", "h3c": "display logbuffer", "ruijie": "show logging"}

# 配置模式包装：进入/退出全局配置视图
_CONFIG_PRE = {"huawei": ["system-view"], "h3c": ["system-view"], "ruijie": ["configure terminal"]}
_CONFIG_POST = {"huawei": ["return"], "h3c": ["return"], "ruijie": ["end"]}
# 保存配置（华为 save 需确认 y；H3C Comware7 save force 免交互；锐捷 write）
SAVE_CMDS = {"huawei": ["save", "y"], "h3c": ["save force"], "ruijie": ["write"]}


# ---------------- 公共解析与执行 ----------------

def _vendor_display(vendor: str) -> str:
    return VENDOR_CN.get(vendor, vendor)


def _resolve_targets(args: dict, device: dict) -> tuple[list[dict] | None, str]:
    """解析目标网络设备：devices 参数（名称/管理IP/ID，支持唯一子串匹配，all/全部=全部网络设备）
    优先，否则使用当前会话绑定的网络设备。返回 (devices, error)。"""
    raw = args.get("devices")
    all_devs = db.list_netdev_devices()
    if raw:
        if isinstance(raw, str):
            raw = [raw]
        resolved, missing, wanted_all = [], [], False
        for item in raw:
            s = str(item or "").strip()
            if not s:
                continue
            if s.lower() in ("all", "*", "全部", "所有", "所有设备", "全部设备"):
                wanted_all = True
                for d in all_devs:
                    if d not in resolved:
                        resolved.append(d)
                continue
            hit = next((d for d in all_devs
                        if d["id"] == s or d["host"] == s or d["name"] == s
                        or (d["name"] or "").lower() == s.lower()), None)
            if hit is None:
                subs = [d for d in all_devs if s.lower() in (d["name"] or "").lower()]
                hit = subs[0] if len(subs) == 1 else None
                if hit is None and len(subs) > 1:
                    missing.append(f"{s}（匹配到多台：{'、'.join(d['name'] for d in subs[:5])}，请用全名）")
                    continue
            if hit and hit not in resolved:
                resolved.append(hit)
        if wanted_all and not all_devs:
            return None, "尚无已添加的网络设备，请先在「网络设备管理」页添加（AI 对话支持华为/H3C/锐捷）"
        if missing:
            return None, ("未找到网络设备：" + "；".join(missing)
                          + f"。可用设备：{'、'.join(d['name'] for d in all_devs) or '（无，请先在「网络设备管理」页添加）'}")
        if not resolved:
            return None, "未提供有效的目标网络设备"
        return resolved, ""
    if device and device_kind(device.get("id", "")) == "netdev" and device.get("host"):
        return [device], ""
    return None, ("当前对话未绑定网络设备，请指定目标网络设备（devices 参数传设备名称或管理IP，"
                  "传 [\"all\"] 表示全部网络设备）。"
                  f"可用设备：{'、'.join(d['name'] for d in all_devs) or '（无，请先在「网络设备管理」页添加）'}")


def _vendor_check(devices: list[dict]) -> str:
    """厂家白名单校验：不在 AI 对话支持范围内的设备返回提示文案。"""
    unsupported = [d for d in devices if (d.get("vendor") or "") not in AI_VENDORS]
    if not unsupported:
        return ""
    names = "、".join(f"{d['name']}（{_vendor_display(d.get('vendor', ''))}）" for d in unsupported)
    return (f"AI 对话管理网络设备目前仅支持华为、H3C、锐捷，以下设备暂不支持：{names}。"
            f"请在「网络设备管理」页面使用批量执行或控制台操作该设备。")


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（输出过长已截断，原文共 {len(text)} 字符；可加关键词过滤缩小范围）"


async def _run_on_targets(devices: list[dict], commands_for, timeout: float) -> dict:
    """在每台目标设备上执行 commands_for(device) 给出的命令，聚合结果。"""
    # 每设备上限自适应：小批量 6000；大批量按 ~20K 总量均摊（下限 500），避免编排器总限截断丢设备
    per_cap = (OUTPUT_CAP_PER_DEVICE if len(devices) <= 3
               else max(500, min(OUTPUT_CAP_PER_DEVICE, 20000 // len(devices))))
    results, ok_count = [], 0
    for d in devices:
        cmds = commands_for(d)
        r = await netdev_service.run_commands(d, cmds, timeout=timeout)
        if r.get("ok"):
            ok_count += 1
            results.append({"device": d["name"], "host": d["host"],
                            "vendor": _vendor_display(d.get("vendor", "")),
                            "ok": True,
                            "commands": cmds,
                            "output": _clip(r.get("output", ""), per_cap)})
        else:
            results.append({"device": d["name"], "host": d["host"],
                            "vendor": _vendor_display(d.get("vendor", "")),
                            "ok": False, "commands": cmds,
                            "error": r.get("error", "执行失败")})
    summary = (f"共 {len(devices)} 台设备，成功 {ok_count} 台、失败 {len(devices) - ok_count} 台。"
               if len(devices) > 1 else
               (f"设备 {devices[0]['name']} 执行成功。" if ok_count else
                f"设备 {devices[0]['name']} 执行失败。"))
    return {"targets": len(devices), "succeeded": ok_count, "results": results,
            "_llm_summary": summary + " 请基于命令回显原文作答：先给结论，再摘录关键行；"
                                       "回显中的版本/利用率/表项数量等数字必须来自原文，不要编造。"}


# devices 批量参数（网络设备版：目标为 netdev_devices 表记录）
_NETDEV_DEVICES_DESC = {
    "type": "array", "items": {"type": "string"},
    "description": ("目标网络设备列表（设备名称或管理IP，可多个实现批量），"
                    "如 [\"核心交换机01\",\"10.20.1.2\"]，传 [\"all\"] 表示全部网络设备；"
                    "缺省对当前会话绑定的网络设备执行（全局模式下必须指定）")}


# ---------------- 查询类工具 handler ----------------

async def _h_list_devices(client, args: dict, device: dict) -> dict:
    rows = db.list_netdev_devices()
    return {"total": len(rows),
            "devices": [{"id": d["id"], "name": d["name"], "vendor": d["vendor"],
                         "vendor_cn": _vendor_display(d["vendor"]), "model": d.get("model", ""),
                         "host": d["host"], "port": d.get("port", 22),
                         "group": d.get("group_name", ""),
                         "ai_chat_supported": d["vendor"] in AI_VENDORS} for d in rows],
            "_llm_summary": ("网络设备清单已返回（ai_chat_supported=false 的厂家不在 AI 对话支持范围）。"
                             "后续对这些设备的操作请用 devices 参数传设备名称或管理IP。")}


async def _h_get_status(client, args: dict, device: dict) -> dict:
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    return await _run_on_targets(devices, lambda d: HEALTH_CMDS[d["vendor"]], timeout=45)


async def _h_get_config(client, args: dict, device: dict) -> dict:
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    kw = str(args.get("keyword") or "").strip()

    def _cmds(d):
        cmd = CONFIG_CMD[d["vendor"]]
        return [f"{cmd} | include {kw}" if kw else cmd]
    return await _run_on_targets(devices, _cmds, timeout=60)


async def _h_get_interfaces(client, args: dict, device: dict) -> dict:
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    name = str(args.get("interface") or "").strip()
    full = expand_ifname(name) if name else ""

    def _cmds(d):
        if name:
            return [IF_DETAIL_CMD[d["vendor"]].format(name=full)]
        return IF_BRIEF_CMDS[d["vendor"]]
    return await _run_on_targets(devices, _cmds, timeout=45)


async def _h_get_routes(client, args: dict, device: dict) -> dict:
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    proto = str(args.get("protocol") or "").strip().lower()

    def _cmds(d):
        if proto:
            return [ROUTE_PROTO_CMD[d["vendor"]].format(proto=proto)]
        return [ROUTE_CMD[d["vendor"]]]
    return await _run_on_targets(devices, _cmds, timeout=45)


async def _h_get_arp(client, args: dict, device: dict) -> dict:
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    ip = str(args.get("ip") or "").strip()

    def _cmds(d):
        cmd = ARP_CMD[d["vendor"]]
        return [f"{cmd} | include {ip}" if ip else cmd]
    return await _run_on_targets(devices, _cmds, timeout=45)


_IF_BAD_OUTPUT = re.compile(
    r"(Unrecognized|Incomplete command|Invalid|参数错误|不存在的接口|找不到该接口|Wrong param)", re.I)


async def _h_get_interface_config(client, args: dict, device: dict) -> dict:
    """接口配置查看（一次性）：接口详情（状态/计数）+ 接口视图 display this（生效配置）。

    接口名支持常用缩写（GE1/0/21 等）：自动展开为厂商全名，失败再回退原名重试。
    """
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    name = str(args.get("interface") or "").strip()
    if not name:
        return {"error": "请提供接口名（interface 参数，如 GigabitEthernet1/0/1，"
                         "也支持常用缩写如 GE1/0/1、XGE1/0/21、BAgg1 等）"}

    results, ok_count = [], 0
    for d in devices:
        candidates, seen = [], set()
        for cand in (expand_ifname(name), name):
            if cand and cand not in seen:
                seen.add(cand)
                candidates.append(cand)
        chosen = None
        for cand in candidates:
            cmds = [c.format(name=cand) for c in IF_VIEW_CONFIG[d["vendor"]]]
            r = await netdev_service.run_commands(d, cmds, timeout=45)
            if not r.get("ok"):
                chosen = (cand, cmds, r, True)
                break
            out = r.get("output", "")
            body = out[len(out) // 3:]   # 跳过命令回显行，检查正文是否有报错特征
            if _IF_BAD_OUTPUT.search(body):
                continue   # 该形态接口名不被识别：换下一个候选（缩写↔全名）
            chosen = (cand, cmds, r, False)
            break
        if chosen is None:   # 所有候选都报错：回传最后一次尝试
            cmds = [c.format(name=candidates[-1]) for c in IF_VIEW_CONFIG[d["vendor"]]]
            r = await netdev_service.run_commands(d, cmds, timeout=45)
            chosen = (candidates[-1], cmds, r, False)
        cand, cmds, r, conn_failed = chosen
        if conn_failed:
            results.append({"device": d["name"], "host": d["host"],
                            "vendor": _vendor_display(d.get("vendor", "")),
                            "ok": False, "commands": cmds,
                            "error": r.get("error", "执行失败")})
            continue
        ok_count += 1
        results.append({"device": d["name"], "host": d["host"],
                        "vendor": _vendor_display(d.get("vendor", "")),
                        "ok": True, "commands": cmds, "interface": cand,
                        "output": _clip(r.get("output", ""), OUTPUT_CAP_PER_DEVICE)})
    summary = (f"已查看 {ok_count}/{len(devices)} 台设备的接口配置（含接口状态与视图内生效配置）。"
               if len(devices) > 1 else
               ("接口配置已返回（含 display this 视图配置与接口状态计数）。" if ok_count
                else "接口查询失败，请确认接口名。"))
    return {"targets": len(devices), "succeeded": ok_count, "results": results,
            "_llm_summary": summary + " 请基于回显原文作答：配置项取自 display this 段，"
                                       "状态/错包计数取自 display interface 段，注明接口名。"}


async def _h_query(client, args: dict, device: dict) -> dict:
    """只读自由查询：仅放行 dis/display/show 前缀命令（网络 CLI 的只读词根），
    免确认卡片——大幅扩展 AI 可查询范围（VLAN/STP/MAC/LLDP/聚合口/光模块/OSPF 邻居等）。"""
    import re as _re
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    raw = args.get("commands") or []
    if isinstance(raw, str):
        raw = [raw]
    commands = [str(c).strip() for c in raw if str(c).strip()]
    if not commands:
        return {"error": "commands 不能为空"}
    readonly = _re.compile(r"^(dis|display|show)\b", _re.IGNORECASE)
    bad = [c for c in commands if not readonly.match(c)]
    if bad:
        return {"error": (f"以下命令不是只读查询（仅放行 dis/display/show 开头）：{bad}。"
                          "变更类请使用 netdev_apply_config / netdev_run_commands（走确认卡片）")}
    return await _run_on_targets(devices, lambda d: commands, timeout=60)


async def _h_get_logs(client, args: dict, device: dict) -> dict:
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    kw = str(args.get("keyword") or "").strip()

    def _cmds(d):
        cmd = LOG_CMD[d["vendor"]]
        return [f"{cmd} | include {kw}" if kw else cmd]
    result = await _run_on_targets(devices, _cmds, timeout=45)
    result["_llm_summary"] = (
        result["_llm_summary"] + " 这是设备日志缓冲区原文，请做故障分析定位："
        "1) 按时间线归纳关键事件（接口翻动/协议震荡/认证失败/资源告警等）；"
        "2) 标注严重级别与可能影响；3) 给出根因假设与下一步排查命令建议。")
    return result


async def _h_locate_terminal(client, args: dict, device: dict) -> dict:
    """终端定位：IP/MAC/设备名 → 接入交换机 + 端口（复用拓扑服务 ARP/MAC 缓存与 search_asset）。"""
    from app.services import netdev_topology_service as topo
    query = str(args.get("query") or "").strip()
    if not query:
        return {"error": "请提供要定位的 IP / MAC / 设备名（query 参数）"}
    group = str(args.get("group") or "").strip()
    all_groups = sorted({(d.get("group_name") or "").strip()
                         for d in db.list_netdev_devices()})
    if not all_groups:
        return {"error": "尚无网络设备，请先在「网络设备管理」页添加"}
    # 指定了分组时先在该组内定位，未命中降级为全网搜索（终端可能接在别的分组的设备上）
    groups = [group] if group else all_groups
    collected = ""
    hits, misses = [], []
    searched = set()

    async def _ensure_fresh(g: str) -> None:
        """缓存为空或已过期（TTL 外）时先重新采集，避免拿陈旧 ARP/MAC 定位失败。"""
        nonlocal collected
        caches = db.list_netdev_topology_cache(g)
        stale = (not caches
                 or not any((c.get("arp") or []) or (c.get("mac") or []) for c in caches)
                 or any(topo._stale(c) for c in caches))
        if stale:
            await topo.collect_group(g)
            collected = "定位前已自动完成过期缓存的重新采集（ARP/MAC 表实时性更好，多花了一些时间）"

    for g in groups:
        searched.add(g)
        await _ensure_fresh(g)
        r = topo.search_asset(g, query)
        if r.get("hits"):
            hits.append({"group": g, **r})
        else:
            misses.append({"group": g, "reason": r.get("reason", "")})
    # 组内未命中（或组内设备缓存齐了仍找不到）：降级搜索其余分组
    if not hits and group:
        for g in all_groups:
            if g in searched or g == group:
                continue
            await _ensure_fresh(g)
            r = topo.search_asset(g, query)
            if r.get("hits"):
                hits.append({"group": g, **r, "note": f"指定分组内未找到，在分组「{g}」中定位到"})
            else:
                misses.append({"group": g, "reason": r.get("reason", "")})
    if not hits:
        reason = next((m["reason"] for m in misses if m.get("reason")), "")
        return {"kind": "none", "query": query, "hits": [],
                "_llm_summary": (f"在 {len(groups)} 个分组的 ARP/MAC 表中未定位到「{query}」。"
                                 f"{reason or ''} 终端可能离线、不在纳管网段，或交换机 ARP/MAC 表尚未采集"
                                 "（可让用户在「网络设备管理→网络拓扑」页重新采集后再试）。"
                                 + (f"{collected}。" if collected else ""))}
    best = hits[0]
    first = (best.get("hits") or [{}])[0]
    kind_text = "为纳管网络设备本身" if best.get("kind") == "device" else "终端/资产"
    line = f"「{query}」定位结果：{kind_text}"
    if first:
        line += f"，接入设备 {first.get('device_name', '')} 端口 {first.get('port', '') or '未知'}"
        if first.get("ip"):
            line += f" IP {first['ip']}"
        if first.get("mac"):
            line += f" MAC {first['mac']}"
    result = {"kind": best.get("kind"), "query": query, "groups": hits,
              "_llm_summary": (f"{line}。请向用户报告：接入设备、端口、MAC/IP（如返回），"
                               f"并说明判定来源（MAC 表=权威接入点，ARP=学习口参考）；"
                               f"多分组命中时按组分别列出。{collected} "
                               "若用户接着要查该接口配置，直接调用 netdev_get_interface_config"
                               "（devices=[接入设备], interface=端口名，缩写自动展开），"
                               "不要用 netdev_get_config 的 keyword 过滤（include 只返回匹配行，"
                               "拿不到配置块）。")}
    return result


# ---------------- 写操作工具（确认卡片 → 执行） ----------------

def _apply_plan_commands(args: dict, device: dict) -> tuple[list[dict] | None, list[str] | None, str, bool, str]:
    """解析配置下发参数：返回 (devices, commands, interface, save, error)。"""
    devices, err = _resolve_targets(args, device)
    if err:
        return None, None, "", False, err
    if msg := _vendor_check(devices):
        return None, None, "", False, msg
    raw = args.get("commands") or []
    if isinstance(raw, str):
        raw = [raw]
    commands = [str(c).strip() for c in raw if str(c).strip()]
    if not commands:
        return None, None, "", False, "commands 不能为空：请提供要下发的配置命令列表"
    interface = str(args.get("interface") or "").strip()
    save = bool(args.get("save"))
    return devices, commands, interface, save, ""


def _wrap_config(vendor: str, commands: list[str], interface: str = "") -> list[str]:
    pre = list(_CONFIG_PRE[vendor])
    post = list(_CONFIG_POST[vendor])
    if interface:
        pre.append(f"interface {interface}")
    return pre + commands + post


def _full_commands(vendor: str, commands: list[str], interface: str, save: bool) -> list[str]:
    cmds = _wrap_config(vendor, commands, interface)
    if save:
        cmds += SAVE_CMDS[vendor]
    return cmds


async def _p_apply_config(client, args: dict, device: dict) -> dict:
    devices, commands, interface, save, err = _apply_plan_commands(args, device)
    if err:
        return {"error": err}
    blocks = []
    for d in devices:
        cmds = _full_commands(d["vendor"], commands, interface, save)
        blocks.append(f"**{d['name']}**（{_vendor_display(d['vendor'])}）将执行：\n```\n"
                      + "\n".join(cmds) + "\n```")
    warning = ("配置将直接进入设备运行配置" + ("并保存" if save else "，默认不保存（设备重启后丢失）")
               + "；请逐条核对命令的影响范围。")
    return {
        "title": f"下发网络设备配置（{len(devices)} 台）" + (f" · 接口 {interface}" if interface else ""),
        "resource": "netdev_config", "resource_cn": "网络设备配置", "op": "create",
        "target_id": interface,
        "before": None,
        "after": {"devices": [d["name"] for d in devices], "interface": interface,
                  "commands": commands, "save": save},
        "fields": ["commands"] + (["interface"] if interface else []) + (["save"] if save else []),
        "conflicts": [],
        "warning": warning,
        "detail": "\n\n".join(blocks),
    }


async def _h_apply_config(client, args: dict, device: dict) -> dict:
    devices, commands, interface, save, err = _apply_plan_commands(args, device)
    if err:
        return {"error": err}

    def _cmds(d):
        return _full_commands(d["vendor"], commands, interface, save)
    result = await _run_on_targets(devices, _cmds, timeout=60)
    db.audit("agent.write.netdev_config.apply",
             {"devices": [d["name"] for d in devices], "interface": interface,
              "commands": commands, "save": save}, result="ok")
    result["_llm_summary"] = (result["_llm_summary"]
                              + (" 配置已保存。" if save else " 配置未保存，设备重启后丢失；"
                                                     "如需保存可说「保存配置」。")
                              + " 请逐台报告执行结果；如设备回显报错，给出修正建议。")
    return result


async def _p_run_commands(client, args: dict, device: dict) -> dict:
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    raw = args.get("commands") or []
    if isinstance(raw, str):
        raw = [raw]
    commands = [str(c).strip() for c in raw if str(c).strip()]
    if not commands:
        return {"error": "commands 不能为空"}
    blocks = [f"**{d['name']}**（{_vendor_display(d['vendor'])}）：\n```\n" + "\n".join(commands) + "\n```"
              for d in devices]
    return {
        "title": f"在 {len(devices)} 台网络设备上执行命令",
        "resource": "netdev_command", "resource_cn": "网络设备命令", "op": "create",
        "target_id": "",
        "before": None,
        "after": {"devices": [d["name"] for d in devices], "commands": commands},
        "fields": ["commands"],
        "conflicts": [],
        "warning": ("命令将原样下发设备（不做配置模式包装）。若包含配置类命令（如进入系统视图修改配置），"
                    "请确认每条命令的影响；查询类建议优先使用专用查询工具。"),
        "detail": "\n\n".join(blocks),
    }


async def _h_run_commands(client, args: dict, device: dict) -> dict:
    devices, err = _resolve_targets(args, device)
    if err:
        return {"error": err}
    if msg := _vendor_check(devices):
        return {"error": msg}
    raw = args.get("commands") or []
    if isinstance(raw, str):
        raw = [raw]
    commands = [str(c).strip() for c in raw if str(c).strip()]
    if not commands:
        return {"error": "commands 不能为空"}
    result = await _run_on_targets(devices, lambda d: commands, timeout=45)
    db.audit("agent.write.netdev_command.run",
             {"devices": [d["name"] for d in devices], "commands": commands}, result="ok")
    return result


# ---------------- 工具注册表（tools.py 底部合并进 TOOLS） ----------------

NETDEV_TOOLS: list[Tool] = [
    Tool("netdev_list_devices", "列出已添加的网络设备（交换机/路由器）：名称、厂家、管理IP、分组，及是否支持 AI 对话（仅华为/H3C/锐捷）。",
         {"type": "object", "properties": {}}, _h_list_devices,
         device_type="netdev", needs_device=False, batch_devices=False),
    Tool("netdev_get_status", "网络设备健康查询（华为/H3C/锐捷）：版本、CPU、内存、环境（温度/风扇/电源）等运行状态。",
         {"type": "object", "properties": {"devices": _NETDEV_DEVICES_DESC}},
         _h_get_status, device_type="netdev", needs_device=False),
    Tool("netdev_get_config", "网络设备配置查询（华为/H3C/锐捷）：查看当前运行配置全文，keyword 过滤基于 | include（只返回包含关键词的行，不返回配置块）——适合 acl/ospf/ntp 等特性级检索；查单个接口的完整配置请改用 netdev_get_interface_config。",
         {"type": "object",
          "properties": {"keyword": {"type": "string", "description": "可选：配置过滤关键词（管道 include）"},
                         "devices": _NETDEV_DEVICES_DESC}},
         _h_get_config, device_type="netdev", needs_device=False),
    Tool("netdev_get_interfaces", "网络设备接口查询（华为/H3C/锐捷）：接口概览（状态/速率/VLAN/IP）或指定接口详情（含收发包/错包）。",
         {"type": "object",
          "properties": {"interface": {"type": "string", "description": "可选：接口名（如 GigabitEthernet0/0/1、GE1/0/1），传则查单接口详情"},
                         "devices": _NETDEV_DEVICES_DESC}},
         _h_get_interfaces, device_type="netdev", needs_device=False),
    Tool("netdev_get_routes", "网络设备路由表查询（华为/H3C/锐捷）：全部路由或按协议过滤（static/ospf/bgp/direct 等）。",
         {"type": "object",
          "properties": {"protocol": {"type": "string", "description": "可选：路由协议 static/ospf/bgp/direct 等"},
                         "devices": _NETDEV_DEVICES_DESC}},
         _h_get_routes, device_type="netdev", needs_device=False),
    Tool("netdev_get_arp", "网络设备 ARP 表项查询（华为/H3C/锐捷）：IP-MAC-端口映射，可用 ip 过滤；配合终端定位分析在线情况。",
         {"type": "object",
          "properties": {"ip": {"type": "string", "description": "可选：按 IP 过滤 ARP 表项"},
                         "devices": _NETDEV_DEVICES_DESC}},
         _h_get_arp, device_type="netdev", needs_device=False),
    Tool("netdev_get_logs", "网络设备故障日志查询与分析（华为/H3C/锐捷）：拉取日志缓冲区最近日志并做故障分析定位，可用 keyword 过滤（如 error/interface）。",
         {"type": "object",
          "properties": {"keyword": {"type": "string", "description": "可选：日志过滤关键词"},
                         "devices": _NETDEV_DEVICES_DESC}},
         _h_get_logs, device_type="netdev", needs_device=False),
    Tool("netdev_get_interface_config", "网络设备接口配置查看（华为/H3C/锐捷）：查看某接口当前生效的完整配置——自动进入接口视图执行 display this（锐捷为 show run interface），这是看单个接口配置的正确姿势（比 dis cu 全量过滤更准）。配合 netdev_get_interfaces 的接口概览先找接口名。",
         {"type": "object",
          "properties": {"interface": {"type": "string", "description": "接口名（如 GigabitEthernet1/0/1、XGE1/0/21、Vlan-interface10、Bridge-Aggregation1），支持设备常用缩写"},
                         "devices": _NETDEV_DEVICES_DESC},
          "required": ["interface"]},
         _h_get_interface_config, device_type="netdev", needs_device=False),
    Tool("netdev_query", "网络设备只读自由查询（华为/H3C/锐捷，免确认）：执行 dis/display/show 开头的任意只读命令并回传原文，用于覆盖专用工具没有的查询。常用命令——VLAN：display vlan brief / show vlan brief；MAC：display mac-address | include <关键字>；STP：display stp brief / show spanning-tree summary；链路聚合：display link-aggregation verbose / show interface aggregation brief；LLDP 邻居：display lldp neighbor brief；光模块：display transceiver interface；设备模块：display device；OSPF/BGP 邻居：display ospf peer / display bgp peer；在线用户：display users；DHCP：display dhcp server statistics。",
         {"type": "object",
          "properties": {"commands": {"type": "array", "items": {"type": "string"},
                                      "description": "只读命令列表（必须 dis/display/show 开头，可用 | include 过滤，可多条一次执行）"},
                         "devices": _NETDEV_DEVICES_DESC},
          "required": ["commands"]},
         _h_query, device_type="netdev", needs_device=False),
    Tool("netdev_locate_terminal", "终端定位：按 IP / MAC / 设备名在全网拓扑（ARP+MAC 表）中定位终端接入的交换机与端口，用于故障定位与资产盘点。缓存过期时会自动重新采集（耗时较长）。",
         {"type": "object",
          "properties": {"query": {"type": "string", "description": "要定位的 IP / MAC / 设备名"},
                         "group": {"type": "string", "description": "可选：限定分组，缺省搜全部分组"}},
          "required": ["query"]},
         _h_locate_terminal, device_type="netdev", needs_device=False, batch_devices=False),
    Tool("netdev_apply_config", "网络设备配置下发（华为/H3C/锐捷，写操作需确认）：在指定设备（可批量）上执行配置命令；传 interface 时自动进入接口视图；默认不保存配置，save=true 时保存。用于接口配置、静态路由、VLAN 等变更。",
         {"type": "object",
          "properties": {"commands": {"type": "array", "items": {"type": "string"},
                                      "description": "要下发的配置命令列表（相对全局/接口视图的命令，如 [\"ip address 192.168.1.1 255.255.255.0\"]）"},
                         "interface": {"type": "string", "description": "可选：接口名，传入后自动进入该接口视图再执行命令"},
                         "save": {"type": "boolean", "description": "是否保存配置（默认 false，重启丢失）"},
                         "devices": _NETDEV_DEVICES_DESC},
          "required": ["commands"]},
         _h_apply_config, prepare=_p_apply_config, write=True,
         device_type="netdev", needs_device=False),
    Tool("netdev_run_commands", "在指定网络设备（可批量）上原样执行任意命令（写操作需确认）。通用兜底：查询类操作优先用专用查询工具；含配置命令时同样会生成确认卡片。",
         {"type": "object",
          "properties": {"commands": {"type": "array", "items": {"type": "string"},
                                      "description": "要执行的命令列表（原样下发，不做配置模式包装）"},
                         "devices": _NETDEV_DEVICES_DESC},
          "required": ["commands"]},
         _h_run_commands, prepare=_p_run_commands, write=True,
         device_type="netdev", needs_device=False),
]
