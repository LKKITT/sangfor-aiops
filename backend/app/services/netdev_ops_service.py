"""网络设备运维服务：配置可视化快照、配置体检、配置备份（文本级）。

边界（与生产安全强相关）：
- 本服务全部为**只读**能力：命令采集（display/show 词根）、文本分析、配置文本存档；
- **不提供任何恢复/下发接口**——网络设备配置回退请走控制台/批量执行人工确认，避免误操作；
- 快照与体检报告走进程内 TTL 缓存 / update_cache，减少重复 SSH 拉取。
"""
import difflib
import hashlib
import json
import re

from app import db
from app.services import netdev_service
from app.services.device_cache import device_cache

# 可视化快照分区（按厂家，全部只读命令；顺序即输出拼接顺序）
SNAPSHOT_SECTIONS = {
    "huawei": [
        ("status", "设备状态", ["display version", "display cpu-usage", "display memory-usage"]),
        ("interfaces", "接口概览", ["display interface brief", "display ip interface brief"]),
        ("vlan", "VLAN", ["display vlan"]),
        ("routes", "路由表", ["display ip routing-table"]),
        ("arp", "ARP 表", ["display arp"]),
        ("mac", "MAC 地址表", ["display mac-address"]),
    ],
    "h3c": [
        ("status", "设备状态", ["display version", "display cpu-usage", "display memory"]),
        ("interfaces", "接口概览", ["display interface brief", "display ip interface brief"]),
        # display vlan 在 Comware 5/7 全系可用；display vlan brief 部分老固件不支持
        ("vlan", "VLAN", ["display vlan"]),
        ("routes", "路由表", ["display ip routing-table"]),
        ("arp", "ARP 表", ["display arp"]),
        ("mac", "MAC 地址表", ["display mac-address"]),
    ],
    "ruijie": [
        ("status", "设备状态", ["show version", "show cpu", "show memory"]),
        ("interfaces", "接口概览", ["show interfaces status", "show ip interface brief"]),
        ("vlan", "VLAN", ["show vlan brief"]),
        ("routes", "路由表", ["show ip route"]),
        ("arp", "ARP 表", ["show ip arp"]),
        ("mac", "MAC 地址表", ["show mac address-table"]),
    ],
}
CONFIG_CMD = {"huawei": "display current-configuration",
              "h3c": "display current-configuration",
              "ruijie": "show running-config"}

# 分区输出上限（字符）：配置块最大，其余表项按需
SECTION_CAP = {"status": 6000, "interfaces": 16000, "vlan": 5000,
               "routes": 16000, "arp": 12000, "mac": 12000, "config": 64000}

# 体检报告缓存时长（秒）与快照 TTL
CHECKUP_CACHE_TTL = 300


def _clip(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n…（本节输出超长已截断，原文 {len(text)} 字符）"


def _pick_body(full_output: str, command: str, all_cmds: list[str]) -> str:
    """从聚合回显中剥出单命令正文（去掉回显行，截到下一个命令回显之前）。"""
    seg = full_output.split(command, 1)[1] if command in full_output else ""
    nxt = len(seg)
    for other in all_cmds:
        if other != command:
            i = seg.find(other)
            if 0 <= i < nxt:
                nxt = i
    return seg[:nxt].strip()


# 设备回显的"命令不支持"类错误（整段仅此提示时该分区无数据，不该当成正文展示）
_CMD_ERR_RE = re.compile(
    r"^(%|unrecognized command|invalid input|unknown command|incomplete command|"
    r"too many parameters)", re.I)


def _is_cmd_error(body: str) -> bool:
    """命令不被设备支持：正文很短且首行是错误提示（正常配置/表项远长于此）。"""
    lines = (body or "").strip().splitlines()
    if not lines or len(body) > 300:
        return False
    return bool(_CMD_ERR_RE.match(lines[0].strip()))


async def collect_snapshot(device: dict, force: bool = False) -> dict:
    """配置可视化快照：一次 SSH 会话采集全部分区（display/show 只读），进程内 TTL 缓存。"""
    device_id = device["id"]

    async def _load() -> dict:
        vendor = (device.get("vendor") or "other").lower()
        sections = SNAPSHOT_SECTIONS.get(vendor, SNAPSHOT_SECTIONS["h3c"])
        config_cmd = CONFIG_CMD.get(vendor, "display current-configuration")

        all_cmds: list[str] = []
        keyed: list[tuple[str, str, str]] = []   # (section_key, section_title, command)
        for key, title, cmds in sections:
            for c in cmds:
                all_cmds.append(c)
                keyed.append((key, title, c))
        all_cmds.append(config_cmd)
        keyed.append(("config", "运行配置", config_cmd))

        r = await netdev_service.run_commands(device, all_cmds, timeout=90)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error", "采集失败")}
        # 同分区多命令合并为一个分区（如 设备状态=version+cpu+memory 三段拼接）
        merged: dict[str, dict] = {}
        order: list[str] = []
        for key, title, cmd in keyed:
            body = _pick_body(r.get("output", ""), cmd, all_cmds)
            if not body or _is_cmd_error(body):
                continue   # 空输出 / 设备不支持该命令（如老固件）直接不出分区
            if key not in merged:
                merged[key] = {"key": key, "title": title, "command": cmd,
                               "parts": [body]}
                order.append(key)
            else:
                merged[key]["parts"].append(body)
        out_sections = []
        for key in order:
            m = merged[key]
            cap = SECTION_CAP.get(key, 8000)
            output = ("\n\n".join(m["parts"]))
            out_sections.append({"key": key, "title": m["title"],
                                 "command": m["command"],
                                 "output": _clip(output, cap),
                                 "truncated": len(output) > cap})
        return {"ok": True, "vendor": vendor, "sections": out_sections,
                "collected_at": db.now()}

    if force:
        device_cache.invalidate(device_id)
    import time
    hit = device_cache._data.get((device_id, "netdev_snapshot"))
    if hit and time.monotonic() - hit[0] < 60:
        return hit[1]
    result = await _load()
    if result.get("ok"):
        device_cache._data[(device_id, "netdev_snapshot")] = (time.monotonic(), result)
    return result


# ---------------- 配置体检（文本规则引擎，输出结构与深信服体检一致） ----------------

def _vty_block_without_acl(cfg: str) -> bool:
    """收集全部 vty 块，任一块内未引用 ACL 即视为命中（块以 #/空行/下一 user-interface 结束）。"""
    lines = cfg.splitlines()
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for line in lines:
        low = line.strip().lower()
        if low.startswith("user-interface vty"):
            if cur:
                blocks.append(cur)
            cur = [low]
        elif cur is not None:
            if low.startswith("#") or low.startswith("user-interface") or (not line.strip()):
                blocks.append(cur)
                cur = None
            else:
                cur.append(low)
    if cur:
        blocks.append(cur)
    return any(not any("acl" in l for l in blk[1:]) for blk in blocks)


def checkup_config(config: str, vendor: str) -> dict:
    """网络设备配置体检：正则规则引擎，输出 {score, grade, counts, items[]}。

    规则覆盖（华为/H3C/锐捷常见口径）：明文/弱口令、SNMP 公共团名、Telnet 开启、
    FTP/HTTP 服务开启、vty 无 ACL、无日志主机、无 NTP、SSH 未启用。
    """
    cfg = config or ""
    low_lines = [(i, l.strip()) for i, l in enumerate(cfg.splitlines())]
    items: list[dict] = []

    def _has(pattern: str) -> bool:
        return re.search(pattern, cfg, re.I | re.M) is not None

    def _enabled_line(keyword: str):
        """命中开启行：关键词词边界匹配（stelnet 不误命中 telnet），排除 undo/no 关闭态。"""
        pat = re.compile(r"(?<![\w-])" + re.escape(keyword), re.I)
        for i, l in low_lines:
            if pat.search(l) and not re.match(r"^(undo|no)\b", l):
                return i, l
        return None

    # 1) 明文/弱口令
    weak = [l for _, l in low_lines
            if re.search(r"password\s+simple\b", l, re.I)
            or re.search(r"(enable|username|user)\s+\S+\s+(password|secret)\s+\d?\s*0\s+\S", l, re.I)
            or re.search(r"snmp-agent community\s+(?:read\s+|write\s+)?(public|private)\b", l, re.I)
            or re.search(r"snmp-server community\s+(public|private)\b", l, re.I)]
    if weak:
        items.append({"severity": "high", "title": "存在明文口令或 SNMP 默认团名",
                      "detail": "、".join(w[:60] for w in weak[:3]),
                      "suggestion": "口令改用 cipher/secret 加密存储；SNMP 团名更换为强随机串并配 ACL 限制网管来源"})
    # 2) Telnet 明文管理
    if (t := _enabled_line("telnet server enable")) or (t := _enabled_line("telnet-server")):
        items.append({"severity": "high", "title": "Telnet 管理已开启（明文传输）",
                      "detail": t[1][:80],
                      "suggestion": "关闭 Telnet 改用 STelnet/SSH（ssh server enable + stelnet server enable）"})
    # 3) FTP/HTTP 文件与管理服务
    for kw, label in (("ftp server enable", "FTP 服务"), ("http server enable", "HTTP 服务"),
                      ("web-server", "Web 服务")):
        if _enabled_line(kw):
            items.append({"severity": "medium", "title": f"{label}处于开启状态",
                          "detail": f"检测到 {kw}",
                          "suggestion": "非必要服务建议关闭（undo/no 对应命令），减少攻击面"})
            break
    # 4) vty 无 ACL 限制
    if "user-interface vty" in cfg.lower() or "line vty" in cfg.lower():
        if _vty_block_without_acl(cfg):
            items.append({"severity": "medium", "title": "VTY 远程登录未绑定 ACL 来源限制",
                          "detail": "user-interface vty / line vty 块内未发现 acl 引用",
                          "suggestion": "为 VTY 线路绑定 ACL，仅允许运维网段登录"})
    # 5) SSH 未启用
    if not _has(r"(stelnet server enable|ssh server enable|enable service ssh-server|ip ssh server)") \
            and "user-interface vty" in cfg.lower():
        items.append({"severity": "medium", "title": "未检测到 SSH 服务启用",
                      "detail": "配置中无 stelnet/ssh server enable",
                      "suggestion": "启用 SSH 并配置认证方式，替代明文远程管理"})
    # 6) 无日志主机
    if not _has(r"(info-center loghost|logging host|info-center log-host)"):
        items.append({"severity": "low", "title": "未配置日志主机",
                      "detail": "日志仅存本机缓冲，重启即失",
                      "suggestion": "配置 info-center loghost / logging host 指向日志服务器"})
    # 7) 无 NTP
    if not _has(r"\bntp"):
        items.append({"severity": "low", "title": "未配置 NTP 时间同步",
                      "detail": "日志时间戳不可信，故障定位时序难对齐",
                      "suggestion": "配置 ntp-service unicast-server / ntp peer 指向内网 NTP 源"})

    counts = {"high": sum(1 for i in items if i["severity"] == "high"),
              "medium": sum(1 for i in items if i["severity"] == "medium"),
              "low": sum(1 for i in items if i["severity"] == "low")}
    score = max(0, 100 - counts["high"] * 25 - counts["medium"] * 10 - counts["low"] * 5)
    grade = "优" if score >= 90 else "良" if score >= 75 else "中" if score >= 60 else "差"
    return {"score": score, "grade": grade, "counts": counts, "items": items}


async def run_checkup(device: dict, force: bool = False) -> dict:
    """体检执行：拉运行配置 → 规则分析 → 结果缓存 update_cache（kind=netdev_checkup）。"""
    device_id = device["id"]
    if not force:
        cached = db.get_update_cache(device_id, "netdev_checkup")
        if cached:
            return cached["payload"]
    vendor = (device.get("vendor") or "other").lower()
    r = await netdev_service.run_commands(device, [CONFIG_CMD.get(vendor, "display current-configuration")],
                                          timeout=60)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "配置拉取失败")}
    output = r.get("output", "")
    # 剥掉回显与提示符行，只留配置正文（首个命令行之后）
    cfg_cmd = CONFIG_CMD.get(vendor, "display current-configuration")
    body = output.split(cfg_cmd, 1)[1] if cfg_cmd in output else output
    report = checkup_config(body, vendor)
    payload = {"ok": True, "vendor": vendor, "checked_at": db.now(), **report}
    db.save_update_cache(device_id, "netdev_checkup", payload, "local")
    return payload


# ---------------- 配置备份（文本级存档；无恢复能力） ----------------

async def create_backup(device: dict, label: str = "") -> dict:
    """配置备份：拉取运行配置全文，存备份记录 + .conf 文件（文本）。"""
    vendor = (device.get("vendor") or "other").lower()
    cfg_cmd = CONFIG_CMD.get(vendor, "display current-configuration")
    r = await netdev_service.run_commands(device, [cfg_cmd], timeout=60)
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "配置拉取失败"))
    output = r.get("output", "")
    body = output.split(cfg_cmd, 1)[1] if cfg_cmd in output else output
    body = body.strip()
    if len(body) < 50:
        raise RuntimeError("回显过短，疑似未取到有效配置（设备返回异常）")

    backup_id = db.new_id("bk_")
    path = db.backup_file_path(backup_id, ".conf")
    path.write_text(body, encoding="utf-8")
    version_hint = ""
    m = re.search(r"(?:version|Version)\s+([\w.\-]+)", body)
    if m:
        version_hint = m.group(1)
    rec = {"id": backup_id, "device_id": device["id"],
           "label": label or f"配置备份-{db.now()[:16]}",
           "kind": "manual", "sw_version": version_hint,
           "snapshot_json": json.dumps({"type": "netdev", "vendor": vendor,
                                        "config": body}, ensure_ascii=False),
           "file_path": str(path), "file_sha256": hashlib.sha256(body.encode()).hexdigest(),
           "created_at": db.now(), "created_by": "user"}
    return db.create_backup(rec)


def diff_backups(a_id: str, b_id: str) -> dict:
    """两份网络设备配置备份的文本差异（unified diff）。"""
    a, b = db.get_backup(a_id), db.get_backup(b_id)
    if not a or not b:
        raise ValueError("备份不存在")
    for bk in (a, b):
        t = json.loads(bk["snapshot_json"] or "{}")
        if t.get("type") != "netdev":
            raise ValueError(f"备份 {bk['id']} 不是网络设备配置备份")
    a_cfg = a["file_path"] and open(a["file_path"], encoding="utf-8").read() \
        or json.loads(a["snapshot_json"]).get("config", "")
    b_cfg = b["file_path"] and open(b["file_path"], encoding="utf-8").read() \
        or json.loads(b["snapshot_json"]).get("config", "")
    diff = list(difflib.unified_diff(a_cfg.splitlines(), b_cfg.splitlines(),
                                     fromfile=f"{a['label']}（{a['created_at']}）",
                                     tofile=f"{b['label']}（{b['created_at']}）", lineterm=""))
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    return {"a": {"id": a_id, "label": a["label"]}, "b": {"id": b_id, "label": b["label"]},
            "added": added, "removed": removed,
            "diff": "\n".join(diff)[:60000] or "两份配置完全一致"}
