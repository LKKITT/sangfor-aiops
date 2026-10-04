"""网络拓扑服务：按分组采集并解析 LLDP 邻居 / ARP 表，构建组内拓扑图与资产索引。

- 采集：复用 netdev_service 的 SSH 通道与连接池，每设备一条会话内顺序跑
  LLDP 命令候选 + ARP 命令，解析结果按设备缓存（netdev_topology_cache，TTL 内复用）；
- 解析：按厂家做宽容正则（Comware/VRP 为主），匹配失败的行静默跳过；
- 拓扑：LLDP 邻居名与设备名归一匹配（全等 / 去分隔符全等），未命中的邻居
  作为「外部节点」虚挂展示；ARP 用于资产（IP/MAC）→ 设备+端口定位。

认证与凭据不落日志；接口出参不含密码。
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta

from app import db
from app.services import netdev_service

log = logging.getLogger("sangfor-agent.netdev.topology")

TOPOLOGY_TTL = timedelta(minutes=15)          # 采集快照有效期
COLLECT_CONCURRENCY = 10                      # 采集并发上限（每设备一条 SSH 会话）

# 每厂家命令候选：按序下发，取回显非空且未报错的第一条（lldp）；arp 固定一条
# H3C Comware：`dis lldp neighbor-information list` 输出含 System Name 的简要表，
# 是邻居匹配的首选数据源（brief 变体在老固件上不支持，verbose 无邻居名）
LLDP_CANDIDATES = {
    "h3c": ["dis lldp neighbor-information list", "display lldp neighbor-information list",
            "display lldp neighbor"],
    "huawei": ["display lldp neighbor brief", "display lldp neighbor"],
    "cisco": ["show lldp neighbors", "show cdp neighbors"],
    "ruijie": ["show lldp neighbors", "show cdp neighbors"],
    "zte": ["show lldp neighbor"],
    "other": ["dis lldp neighbor-information list", "display lldp neighbor brief",
              "display lldp neighbor", "show lldp neighbors"],
}
ARP_COMMANDS = {
    "h3c": ["display arp"],
    "huawei": ["display arp"],
    "cisco": ["show ip arp"],
    "ruijie": ["show ip arp"],
    "zte": ["show arp"],
    "other": ["display arp", "show ip arp"],
}
# MAC 地址表：接入定位的权威依据（终端从哪个物理口进来）
MAC_CANDIDATES = {
    "h3c": ["display mac-address"],
    "huawei": ["display mac-address"],
    "cisco": ["show mac address-table"],
    "ruijie": ["show mac address-table"],
    "zte": ["show mac-address"],
    "other": ["display mac-address", "show mac address-table"],
}
MAC_TABLE_MAX_ROWS = 5000   # 单设备解析上限保护（异常巨表截断）

_OUTPUT_ERR_HINTS = re.compile(r"(%Unrecognized|Unrecognized command|Incomplete command|% Invalid|% Wrong)", re.I)


def _norm_name(s: str) -> str:
    """设备名归一：大写 + 去分隔符与 VRP 尖括号包裹（<hostname>），用于 LLDP 邻居名匹配。"""
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", (s or "").strip().strip("<>").upper())


# ---------------- 解析器 ----------------

# 接口名 token：GE1/0/17、XGE0/0/1、40GE1/0/1、GigabitEthernet1/0/17、
# Ten-GigabitEthernet1/0/1、Vlanif1、MEth0/0/1、Eth-Trunk1 等。
# 约束：可选 1-3 位数字前缀（40GE/100GE）；`-` 两侧必须为字母（排除
# WW-14F-5800-32F-1 这类主机名）；整串必须含数字（排除 Local/Intf 等表头词）。
_PORT_TOKEN = r"(?:\d{1,3})?(?=[A-Za-z-]*\d)[A-Za-z][A-Za-z]*(?:-[A-Za-z][A-Za-z]*)*\d*(?:/\d+)*(?::\d+)?"
_MAC_RE = r"(?:[0-9A-Fa-f]{4}(?:-[0-9A-Fa-f]{4}){2}|[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4})"
_IP_RE = r"(?:\d{1,3}\.){3}\d{1,3}"


def _parse_lldp_list_table(output: str) -> list[dict]:
    """解析 `display lldp neighbor-information list` 表格（H3C Comware）。

    表头列序有两种（Local Interface 在前 / System Name 在前），按表头词的
    字符偏移切列，兼容各种列宽；Chassis ID 的 * # 多邻居标记剔除。
    """
    rows: list[dict] = []
    col_pos: list[int] | None = None
    order: list[str] = []
    for line in output.splitlines():
        if 'Local Interface' in line and 'Chassis ID' in line:
            found = {line.find(n): n for n in ('System Name', 'Local Interface', 'Chassis ID', 'Port ID')
                     if line.find(n) >= 0}
            col_pos = sorted(found)
            order = [found[p] for p in col_pos]
            continue
        if not col_pos or not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith(('Chassis ID :', 'Chassis ID:')) or stripped.startswith(('--', '==' )):
            continue
        values = []
        for i, start in enumerate(col_pos):
            end = col_pos[i + 1] if i + 1 < len(col_pos) else len(line)
            values.append(line[start:end].strip())
        if len(values) != len(order):
            continue
        rec = dict(zip(order, values, strict=True))
        local_port = rec.get('Local Interface', '')
        sysname = rec.get('System Name', '')
        chassis = rec.get('Chassis ID', '').lstrip('*# ')
        port_id = rec.get('Port ID', '')
        if not local_port or not sysname:
            continue
        rows.append({
            'local_port': local_port,
            'neighbor': sysname[:64],
            'neighbor_port': _clean_port_text(port_id),
            'neighbor_mac': chassis if re.fullmatch(_MAC_RE, chassis) else '',
        })
    return rows


def _clean_port_text(p: str) -> str:
    p = (p or '').strip()
    if re.fullmatch(_MAC_RE, p) or re.fullmatch(r'\d+', p):
        return ''   # PortID 为 MAC/纯数字时无端口语义
    return p[:40]


def parse_lldp(vendor: str, output: str) -> list[dict]:
    """解析 LLDP 邻居表，统一产出 {local_port, neighbor, neighbor_port, neighbor_mac}。

    支持三种形态（按序尝试）：
    - list 表格（H3C `dis lldp neighbor-information list`，含 System Name，首选）；
    - brief 表格式（华为/新 Comware）：LocalIntf NeighborDev NeighborIntf [Exptime]；
    - 块状（老 Comware `display lldp neighbor`）：ChassisID/PortID 行，可能无邻居名。
    解析失败的行静默跳过。
    """
    # 首选：list 表格（表头含 Local Interface + Chassis ID + System Name）
    if 'Local Interface' in output and 'System Name' in output and 'Chassis ID' in output:
        rows = _parse_lldp_list_table(output)
        if rows:
            return rows
    out: list[dict] = []
    lines = [l.rstrip() for l in (output or "").splitlines()]

    def _clean_port(p: str) -> str:
        # 去掉 subtype 说明尾巴；剩余为纯数字/MAC 时无端口语义，置空
        p = re.sub(r"/(?:Port component|MAC address|Interface name|Local|Interface).*$",
                   "", (p or "").strip(), flags=re.I)
        p = p.strip()
        if re.fullmatch(_MAC_RE, p) or re.fullmatch(r"\d+", p):
            return ""
        return p[:40]

    # ---- 形态一：块状（检测块头） ----
    block_rows: list[dict] = []
    cur: dict | None = None
    for line in lines:
        hm = re.search(rf"of port\s+\d*\s*\[({_PORT_TOKEN})\]", line, re.I)
        if hm or re.search(r"neighbor.{0,30}(information|brief|list) of", line, re.I):
            if cur and cur.get("local_port"):
                block_rows.append(cur)
            cur = {"local_port": hm.group(1) if hm else "", "neighbor": "",
                   "neighbor_port": "", "neighbor_mac": ""}
            continue
        if cur is None:
            continue
        cm = re.search(r"chassis\s*id[^:：]*[:：]\s*(" + _MAC_RE + r")", line, re.I)
        if cm and not cur["neighbor_mac"]:
            cur["neighbor_mac"] = cm.group(1)
        sm = re.search(r"(?:system\s*name|sysname)\s*[:：]\s*(\S.*)", line, re.I)
        if sm and not cur["neighbor"]:
            cur["neighbor"] = sm.group(1).strip()[:64]
        pm = re.search(r"port\s*id[^:：]*[:：]\s*(\S.*)", line, re.I)
        if pm and not cur["neighbor_port"]:
            cur["neighbor_port"] = _clean_port(pm.group(1))
        if re.match(r"^LLDP neighbor-information of", line, re.I):
            continue
    if cur and cur.get("local_port"):
        block_rows.append(cur)

    for r in block_rows:
        if r.get("neighbor") or r.get("neighbor_mac"):
            out.append(r)

    if out:
        return out

    # ---- 形态二：brief 表格式 ----
    for line in lines:
        cells = line.split()
        if len(cells) < 3:
            continue
        if not re.fullmatch(_PORT_TOKEN, cells[0] or ""):
            continue
        if re.fullmatch(_PORT_TOKEN, cells[-2] or "") and not re.fullmatch(_PORT_TOKEN, cells[1] or ""):
            # 华为 brief：LocalIntf NeighborDev... NeighborIntf Exptime
            local_port, neighbor_port = cells[0], cells[-2]
            neighbor = " ".join(cells[1:-2]) or "?"
            if neighbor and not re.match(r"^\d+(\.\d+)?$", neighbor):
                out.append({"local_port": local_port, "neighbor": neighbor,
                            "neighbor_port": neighbor_port, "neighbor_mac": ""})
        else:
            # 新 Comware brief：LocalIntf NeighborIntf SysName
            ports = [c for c in cells[1:] if re.fullmatch(_PORT_TOKEN, c)]
            if not ports:
                continue
            local_port = cells[0]
            neighbor_port = ports[0]
            start = cells.index(neighbor_port) + 1
            neighbor = " ".join(cells[start:]) or "?"
            if neighbor and neighbor != local_port:
                out.append({"local_port": local_port, "neighbor": neighbor,
                            "neighbor_port": neighbor_port, "neighbor_mac": ""})
    # 过滤畸形行：提示符 `<hostname>` 混入产生的尖括号邻居/端口（自引用噪声）
    return [r for r in out
            if "<" not in (r["local_port"] or "") and ">" not in (r["local_port"] or "")
            and not (r["neighbor"] or "").startswith("<")]


_IFACE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9]{0,7}(?:/\d+)+|Vlanif\d+|MEth\d[\w/\-]*|Eth-Trunk\d+|LoopBack\d+|Vlan\d+)\b",
    re.I)


def parse_mac_table(vendor: str, output: str) -> list[dict]:
    """解析 MAC 地址表：行内取第一个 MAC + 行内最后一个接口形态 token。

    兼容 H3C（MAC VLAN State Port Aging，含空行分隔）与华为
    （MAC VLAN/VSI/BD Learned-From Type，多行折叠——折叠续行无 MAC+端口对自然跳过）。
    """
    out: list[dict] = []
    for line in (output or "").splitlines():
        mm = re.search(rf"({_MAC_RE})\b", line)
        if not mm:
            continue
        mac = _norm_mac(mm.group(1))
        if len(mac) != 12:
            continue
        rest = line[mm.end():]
        im = _IFACE_RE.search(rest)
        if not im:
            continue
        out.append({"mac": mac, "port": im.group(1)})
        if len(out) >= MAC_TABLE_MAX_ROWS:
            break
    seen, uniq = set(), []
    for e in out:
        k = (e["mac"], e["port"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def parse_arp(vendor: str, output: str) -> list[dict]:
    """解析 ARP 表：按行取 IP + MAC，接口取行中第一个接口形态 token。

    容忍列间多空格、Aging/Type/VPN 干扰列、华为 VLAN 折叠行（VLAN 在续行，
    续行仅含 VLAN 数字会被自然跳过——不含 IP+MAC 对）。
    """
    out: list[dict] = []
    for line in (output or "").splitlines():
        pair = re.search(rf"({_IP_RE})\s+({_MAC_RE})\b", line)
        if not pair:
            continue
        ip, mac = pair.group(1), pair.group(2).replace("-", ":").lower()
        rest = line[pair.end():]
        im = _IFACE_RE.search(rest)
        if not im:
            continue
        port = im.group(1)
        out.append({"ip": ip, "mac": mac, "port": port})
    seen, uniq = set(), []
    for e in out:
        k = (e["ip"], e["mac"], e["port"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


# ---------------- 采集 ----------------

def _split_command_outputs(joined: str, dev_name: str, host: str, commands: list[str]) -> dict:
    """把 run_commands 聚合输出按 `name@host> cmd` 段头切回 {cmd: output}。"""
    marker = f"{dev_name}@{host}> "
    segs, current_cmd, acc = {}, None, []
    for line in joined.splitlines():
        if line.startswith(marker):
            if current_cmd is not None:
                segs.setdefault(current_cmd, "\n".join(acc))
            current_cmd, acc = line[len(marker):].strip(), []
        elif current_cmd is not None:
            acc.append(line)
    if current_cmd is not None:
        segs.setdefault(current_cmd, "\n".join(acc))
    return segs


async def collect_device(device: dict, timeout: float = 45) -> dict:
    """单设备采集：一次 SSH 会话内跑 LLDP 候选 + ARP + MAC 地址表，解析并缓存。"""
    vendor = (device.get("vendor") or "other").lower()
    lldp_cmds = LLDP_CANDIDATES.get(vendor, LLDP_CANDIDATES["other"])
    arp_cmds = ARP_COMMANDS.get(vendor, ARP_COMMANDS["other"])
    mac_cmds = MAC_CANDIDATES.get(vendor, MAC_CANDIDATES["other"])
    commands = [*lldp_cmds, *arp_cmds, *mac_cmds]
    result = await netdev_service.run_commands(device, commands, timeout=timeout, reuse_conn=True)
    if not result.get("ok"):
        return {"device_id": device["id"], "ok": False, "error": result.get("error", ""),
                "lldp": [], "arp": [], "mac": []}
    segs = _split_command_outputs(result.get("output", ""),
                                  device["name"], device["host"], commands)

    def _pick(cmds):
        """第一个回显非空且未报错的命令段（剥掉命令回显行）。"""
        for c in cmds:
            seg = segs.get(c, "")
            body = seg[len(c):].strip() if seg.startswith(c) else seg
            if len(body) > 20 and not _OUTPUT_ERR_HINTS.search(body):
                return body
        return ""

    lldp_parsed, arp_parsed, mac_parsed = [], [], []
    try:
        if (lldp_out := _pick(lldp_cmds)):
            lldp_parsed = parse_lldp(vendor, lldp_out)
    except Exception as e:   # noqa: BLE001 —— 单表解析失败不影响其它
        log.warning("LLDP 解析失败 device=%s: %s", device["id"], e)
    try:
        if (arp_out := _pick(arp_cmds)):
            arp_parsed = parse_arp(vendor, arp_out)
    except Exception as e:   # noqa: BLE001
        log.warning("ARP 解析失败 device=%s: %s", device["id"], e)
    try:
        if (mac_out := _pick(mac_cmds)):
            mac_parsed = parse_mac_table(vendor, mac_out)
    except Exception as e:   # noqa: BLE001
        log.warning("MAC 表解析失败 device=%s: %s", device["id"], e)
    db.save_netdev_topology_cache(device["id"], device.get("group_name", ""),
                                  lldp_parsed, arp_parsed, mac_parsed)
    return {"device_id": device["id"], "ok": True, "lldp": lldp_parsed, "arp": arp_parsed,
            "mac": mac_parsed, "lldp_raw_len": len(lldp_out), "arp_raw_len": len(arp_out)}


def _stale(rec: dict | None) -> bool:
    if not rec or not rec.get("fetched_at"):
        return True
    try:
        fetched = datetime.fromisoformat(rec["fetched_at"])
    except ValueError:
        return True
    return datetime.now() - fetched > TOPOLOGY_TTL


async def collect_group(group: str, force: bool = False) -> dict:
    """按分组采集：TTL 内直接用缓存；force=True 全量重采。返回采集统计。"""
    devices = [d for d in db.list_netdev_devices(group) if d.get("host")]
    stats = {"total": len(devices), "collected": 0, "failed": 0}
    if not devices:
        return stats
    targets: list[dict] = []
    if force:
        targets = devices
    else:
        for d in devices:
            if _stale(db.get_netdev_topology_cache(d["id"])):
                targets.append(d)
    stats["collected"] = len(devices) - len(targets)
    if targets:
        sem = asyncio.Semaphore(COLLECT_CONCURRENCY)

        async def one(d):
            async with sem:
                r = await collect_device(d)
                return r

        results = await asyncio.gather(*[one(d) for d in targets], return_exceptions=True)
        for r in results:
            if isinstance(r, BaseException):
                stats["failed"] += 1
            elif r.get("ok"):
                stats["collected"] += 1
            else:
                stats["failed"] += 1
    return stats


# ---------------- 拓扑构建与资产定位 ----------------

def _device_match_index(devices: list[dict]) -> dict:
    """归一名 → 设备 的匹配表（name 与 host 双索引）。"""
    idx: dict[str, dict] = {}
    for d in devices:
        for k in (_norm_name(d.get("name")), _norm_name(d.get("host"))):
            if len(k) >= 3:
                idx[k] = d
    return idx


def build_graph(group: str) -> dict:
    """构建分组拓扑：nodes（设备+外部邻居）、edges（LLDP）、arp_index（资产定位索引）。

    邻居匹配链：① 邻居系统名归一匹配设备名/管理 IP；② 邻居 ChassisID MAC 在全网
    ARP 索引中反查所在设备；③ 都失败 → 匿名外部节点（标签带 MAC 尾号）。
    """
    devices = [d for d in db.list_netdev_devices(group) if d.get("host")]
    # 匹配索引覆盖全网设备：跨分组邻居（如互联网设备看到政务网核心）也链接到真实设备
    all_devices = [d for d in db.list_netdev_devices() if d.get("host")]
    by_id = {d["id"]: d for d in all_devices}
    match_idx = _device_match_index(all_devices)
    caches = {c["device_id"]: c for c in db.list_netdev_topology_cache(group)}
    known = set(caches)
    for d in all_devices:   # 跨组缓存纳入（MAC 反查索引与跨组节点 LLDP 需要）
        if d["id"] not in known:
            c = db.get_netdev_topology_cache(d["id"])
            if c:
                caches[d["id"]] = c

    # 全网 MAC 索引（ARP 学到的 MAC → 所在设备）：用于 LLDP 只有 ChassisID 时的反查
    mac_index: dict[str, str] = {}
    for c in caches.values():
        for a in c.get("arp") or []:
            nm = _norm_mac(a.get("mac", ""))
            if len(nm) == 12 and nm not in mac_index:
                mac_index[nm] = c["device_id"]

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    edge_seen: set[tuple] = set()
    arp_index: list[dict] = []

    def _device_node(d, cross=False):
        return {"id": d["id"], "kind": "device", "name": d["name"], "vendor": d.get("vendor", ""),
                "model": d.get("model", ""), "host": d.get("host", ""), "group": d.get("group_name", ""),
                "last_ok_at": d.get("last_ok_at", ""), "cross": cross}

    for d in devices:
        nodes[d["id"]] = _device_node(d)
    # 第一遍：收集有向 LLDP 报告（设备↔设备 与 设备↔外部邻居），再合并物理链路
    reports: list[dict] = []
    for d in devices:
        cache = caches.get(d["id"])
        if not cache:
            continue
        for e in cache.get("lldp") or []:
            # 剥掉 VRP 尖括号包裹（华为邻居名形如 <hostname>）再匹配/展示
            neighbor = (e.get("neighbor") or "").strip().strip("<>")
            neighbor_mac = _norm_mac(e.get("neighbor_mac", ""))
            target = match_idx.get(_norm_name(neighbor)) if neighbor else None
            tgt_port = e.get("neighbor_port", "")
            if target and target["id"] == d["id"]:
                continue   # 自引用畸形行（如提示符被误解析为邻居），跳过
            if target:
                tgt_id = target["id"]
                if tgt_id not in nodes:   # 跨分组设备：以可操作的真实设备节点呈现
                    nodes[tgt_id] = _device_node(by_id[tgt_id], cross=True)
            elif not target and neighbor_mac and len(neighbor_mac) == 12 \
                    and mac_index.get(neighbor_mac) not in (None, d["id"]):
                tgt = by_id.get(mac_index[neighbor_mac])
                if not tgt:
                    continue
                tgt_id = tgt["id"]
                if tgt_id not in nodes:
                    nodes[tgt_id] = _device_node(tgt, cross=True)
            else:
                if neighbor:
                    ext_key = "ext:" + _norm_name(neighbor)
                    label = neighbor
                elif neighbor_mac:
                    ext_key = "ext:" + neighbor_mac
                    label = f"邻居·{neighbor_mac[-4:]}"
                else:
                    continue
                if ext_key not in nodes:
                    nodes[ext_key] = {"id": ext_key, "kind": "external", "name": label,
                                      "vendor": "", "model": "", "host": "", "group": group}
                tgt_id = ext_key
            reports.append({"frm": d["id"], "frm_port": (e.get("local_port") or "").strip(),
                            "to": tgt_id, "to_port": (tgt_port or "").strip()})
        for a in cache.get("arp") or []:
            arp_index.append({"device_id": d["id"], "device_name": d["name"],
                              "ip": a.get("ip", ""), "mac": a.get("mac", ""),
                              "port": a.get("port", "")})

    links = merge_physical_links(reports)
    # 聚合归并：同对设备 ≥3 条链路视为链路聚合（Eth-Trunk/堆叠线束），合并为 1 条聚合线
    # （单链路/双链路保留原样——双链路是常见的主备/上联形态）
    pair_map: dict[tuple, list[dict]] = {}
    ext_links: list[dict] = []
    for lk in links:
        if lk["frm"].startswith("ext:") or lk["to"].startswith("ext:"):
            ext_links.append(lk)
            continue
        pair_map.setdefault(tuple(sorted([lk["frm"], lk["to"]])), []).append(lk)
    merged_links: list[dict] = []
    for _pair, lks in pair_map.items():
        if len(lks) >= 3:
            first = lks[0]
            merged_links.append({"frm": first["frm"], "to": first["to"],
                                 "frm_port": "", "to_port": "",
                                 "confirmed": all(l["confirmed"] for l in lks),
                                 "aggregated": len(lks)})
        else:
            merged_links.extend(lks)
    merged_links.extend(ext_links)

    edge_seen: set[tuple] = set()
    for lk in merged_links:
        src, dst = sorted([lk["frm"], lk["to"]])
        ek = (src, dst, _norm_port(lk["frm_port"]) or id(lk))
        if ek in edge_seen:
            continue
        edge_seen.add(ek)
        # 展示方向保持报告方向（frm → to）
        edges.append({
            "source": lk["frm"], "target": lk["to"],
            "from_port": lk["frm_port"], "to_port": lk["to_port"],
            "source_name": (by_id.get(lk["frm"]) or nodes.get(lk["frm"]) or {}).get("name", lk["frm"]),
            "target_name": (by_id.get(lk["to"]) or nodes.get(lk["to"]) or {}).get("name", lk["to"]),
            "confirmed": lk["confirmed"], "aggregated": lk.get("aggregated", 0),
        })

    # 连接度统计与核心设备识别：度数按「对端邻居数」计（聚合线算 1 条）；
    # 每个分组内度数最高者为该组核心，第二名 ≥ 第一名 60% 时并列为双核心
    degree: dict[str, int] = {}
    for e in edges:
        for a, b in ((e["source"], e["target"]), (e["target"], e["source"])):
            if a != b:
                degree.setdefault(a, set()).add(b)
    deg_n = {k: len(v) for k, v in degree.items()}
    core_ids: set[str] = set()
    groups_here: dict[str, list] = {}
    for d in devices:
        groups_here.setdefault(d.get("group_name", ""), []).append(d)
    for _grp, grp_devs in groups_here.items():
        ranked = sorted(grp_devs, key=lambda d: deg_n.get(d["id"], 0), reverse=True)
        top = deg_n.get(ranked[0]["id"], 0)
        if top >= 2:
            core_ids.add(ranked[0]["id"])
            if len(ranked) > 1 and deg_n.get(ranked[1]["id"], 0) >= top * 0.6:
                core_ids.add(ranked[1]["id"])
    degree = deg_n
    for n in nodes.values():
        if n["kind"] == "device":
            n["degree"] = degree.get(n["id"], 0)
            n["core"] = n["id"] in core_ids

    positions = db.get_netdev_topology_positions(group)
    fetched = [c.get("fetched_at", "") for c in caches.values() if c.get("fetched_at")]
    return {
        "group": group,
        "nodes": list(nodes.values()),
        "edges": edges,
        "arp_index": arp_index,
        "positions": positions,
        "stats": {
            "devices": len(devices), "links": len(edges),
            "arp_entries": len(arp_index),
            "cached_devices": len([d for d in devices if d["id"] in caches]),
            "fetched_at": max(fetched) if fetched else "",
        },
    }


def _norm_mac(mac: str) -> str:
    return re.sub(r"[^0-9a-f]", "", (mac or "").lower())


def _norm_port(p: str) -> str:
    """端口名归一：全称→缩写、大写、去空格连字符，用于跨厂商端口语义比对。

    GigabitEthernet1/0/7 与 GE1/0/7、Ten-GigabitEthernet1/0/1 与 XGE1/0/1 视为同口。
    """
    s = re.sub(r"[\s\-]", "", (p or "")).upper()
    for full, abbr in (("XGIGABITETHERNET", "XGE"), ("TENGIGABITETHERNET", "XGE"),
                       ("10GIGABITETHERNET", "XGE"), ("25GIGABITETHERNET", "25GE"),
                       ("40GIGABITETHERNET", "40GE"), ("100GIGABITETHERNET", "100GE"),
                       ("GIGABITETHERNET", "GE"), ("ETHERNET", "ETH"), ("GIGABIT", "GE")):
        s = s.replace(full, abbr)
    return s


def merge_physical_links(reports: list[dict]) -> list[dict]:
    """把双向 LLDP 报告合并为物理链路。

    同一条线缆两端各报告一次（A:自口p→对端q；B:自口q→对端p），互证条件：
    任一端宣告的对端端口归一后等于对端自报端口。互证成功 → 单条确认链路；
    双端报告但端口对不上（格式差异）→ 按设备对合并为一条（低置信确认）；
    仅单端报告 → 单侧链路（confirmed=False）。真双链路（两对互证）自然产出两条。
    """
    links: list[dict] = []
    used = [False] * len(reports)

    # 一阶段：互证配对
    for i, r1 in enumerate(reports):
        if used[i]:
            continue
        for j in range(i + 1, len(reports)):
            if used[j]:
                continue
            r2 = reports[j]
            if r2["frm"] != r1["to"] or r2["to"] != r1["frm"]:
                continue
            cross1 = _norm_port(r1["to_port"]) and _norm_port(r1["to_port"]) == _norm_port(r2["frm_port"])
            cross2 = _norm_port(r2["to_port"]) and _norm_port(r2["to_port"]) == _norm_port(r1["frm_port"])
            if cross1 or cross2:
                used[i] = used[j] = True
                links.append({"frm": r1["frm"], "to": r1["to"],
                              "frm_port": r1["frm_port"], "to_port": r2["frm_port"],
                              "confirmed": True})
                break

    # 二阶段：残留的双方报告（端口格式对不上）按设备对合并
    leftover = [r for i, r in enumerate(reports) if not used[i]]
    used2 = [False] * len(leftover)
    for i, r1 in enumerate(leftover):
        if used2[i] or r1["to"].startswith("ext:"):
            continue
        for j in range(i + 1, len(leftover)):
            if used2[j]:
                continue
            r2 = leftover[j]
            if r2["frm"] == r1["to"] and r2["to"] == r1["frm"] and not r2["to"].startswith("ext:"):
                used2[i] = used2[j] = True
                links.append({"frm": r1["frm"], "to": r1["to"],
                              "frm_port": r1["frm_port"], "to_port": r2["frm_port"],
                              "confirmed": True})
                break

    # 三阶段：单侧报告
    for i, r in enumerate(leftover):
        if not used2[i]:
            links.append({"frm": r["frm"], "to": r["to"],
                          "frm_port": r["frm_port"], "to_port": r["to_port"],
                          "confirmed": False})
    return links


def _locate_mac_in_group(group: str, target_mac: str, devices: list[dict],
                         caches: dict) -> tuple[list[dict], list[dict]]:
    """在分组设备的 MAC 地址表 / ARP 中定位一个 MAC。

    返回 (权威接入命中, 其他学习点)：
    - MAC 表命中里，端口**不是** LLDP 上联口（该端口无 LLDP 邻居）的为直连接入口，
      即权威接入位置；其余（含 ARP 学到该 MAC 的路径设备）作为参考。
    """
    access_hits: list[dict] = []
    learn_points: list[dict] = []
    for d in devices:
        c = caches.get(d["id"])
        if not c:
            continue
        # 该设备的 LLDP 本地端口集合 = 上联/互联口（有邻居的口）
        lldp_ports = {_norm_port(e.get("local_port")) for e in c.get("lldp") or [] if e.get("local_port")}
        for m in c.get("mac") or []:
            if m.get("mac") == target_mac:
                hit = {"device_id": d["id"], "device_name": d["name"], "port": m.get("port", ""),
                       "ip": "", "mac": target_mac, "source": "mac-table",
                       "access": _norm_port(m.get("port")) not in lldp_ports}
                (access_hits if hit["access"] else learn_points).append(hit)
        for a in c.get("arp") or []:
            if _norm_mac(a.get("mac")) == target_mac:
                hit = {"device_id": d["id"], "device_name": d["name"], "port": a.get("port", ""),
                       "ip": a.get("ip", ""), "mac": target_mac, "source": "arp",
                       "access": _norm_port(a.get("port")) not in lldp_ports}
                (access_hits if hit["access"] else learn_points).append(hit)
    return access_hits, learn_points


def search_asset(group: str, query: str) -> dict:
    """资产定位：IP / MAC → 权威接入交换机与端口。

    策略：
    1. 输入为设备管理 IP → 直接定位设备节点（kind=device）；
    2. IP → 经 ARP 解析出 MAC → MAC 地址表定位接入端口（权威）；
    3. MAC 直接搜索同理；
    4. MAC 表无命中时降级 ARP 启发式（非上联口优先）。
    返回 {kind, hits(排序后，首位为权威接入点), arp_refs(ARP 学习点参考), primary_hit}。
    """
    q = (query or "").strip()
    if not q:
        return {"kind": "none", "hits": [], "arp_refs": [], "primary_hit": None}
    nq_mac = _norm_mac(q)
    is_ip = re.fullmatch(_IP_RE, q)
    devices = [d for d in db.list_netdev_devices(group) if d.get("host")]
    caches = {c["device_id"]: c for c in db.list_netdev_topology_cache(group)}

    # 0) 管理 IP 短路：IP 是某设备的 loopback/管理地址
    if is_ip:
        owner = next((d for d in devices if d["host"] == q), None)
        if owner:
            return {"kind": "device",
                    "hits": [{"device_id": owner["id"], "device_name": owner["name"], "port": "管理地址",
                              "ip": owner["host"], "mac": "", "access": True, "source": "mgmt"}],
                    "arp_refs": [], "primary_hit": owner["name"]}

    # 1) 解析目标 MAC
    target_mac = None
    if not is_ip and nq_mac and len(nq_mac) >= 12:
        target_mac = nq_mac
    elif is_ip:
        mac_votes: dict[str, int] = {}
        for d in devices:
            for a in (caches.get(d["id"]) or {}).get("arp") or []:
                if a.get("ip") == q and _norm_mac(a.get("mac")):
                    k = _norm_mac(a["mac"])
                    mac_votes[k] = mac_votes.get(k, 0) + 1
        if mac_votes:
            target_mac = max(mac_votes, key=mac_votes.get)

    if not target_mac:
        # IP 无 ARP 记录：可能是离线终端或网段外地址
        if is_ip:
            return {"kind": "none", "hits": [], "arp_refs": [], "primary_hit": None,
                    "reason": f"ARP/MAC 表中未找到 {q}，终端可能离线或不在本组网内"}
        # MAC 前缀输入
        if nq_mac and len(nq_mac) >= 6:
            partial = [h for h in _group_arp(group) if nq_mac in _norm_mac(h["mac"])][:20]
            return {"kind": "asset", "hits": partial, "arp_refs": [],
                    "primary_hit": partial[0]["device_name"] if partial else None}
        # 设备名 / 主机名模糊匹配
        nq = _norm_name(q)
        hits = []
        for d in devices:
            if nq and (nq in _norm_name(d["name"]) or nq in _norm_name(d["host"])):
                hits.append({"device_id": d["id"], "device_name": d["name"], "port": "管理地址",
                             "ip": d["host"], "mac": "", "access": True, "source": "mgmt"})
        if hits:
            return {"kind": "device", "hits": hits[:50], "arp_refs": [],
                    "primary_hit": hits[0]["device_name"]}
        return {"kind": "none", "hits": [], "arp_refs": [], "primary_hit": None}

    # 2) MAC 表 / ARP 联合定位
    access_hits, learn_points = _locate_mac_in_group(group, target_mac, devices, caches)
    all_hits = access_hits + learn_points

    # 3) 附上 IP 信息（ARP 里反查该 MAC 的 IP）
    ip_by_mac: dict[str, str] = {}
    for d in devices:
        for a in (caches.get(d["id"]) or {}).get("arp") or []:
            nm = _norm_mac(a.get("mac"))
            if nm == target_mac and a.get("ip") and nm not in ip_by_mac:
                ip_by_mac[nm] = a["ip"]
    for h in all_hits:
        h["ip"] = h.get("ip") or ip_by_mac.get(target_mac, "")
        h["mac"] = target_mac

    # 排序：MAC 表接入命中 > MAC 表上联命中 > ARP 接入 > ARP 上联；同级按设备度数升序（叶子优先）
    deg = {d["id"]: 0 for d in devices}
    g = build_graph(group)
    for e in g["edges"]:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    def _rank(h):
        src_rank = 0 if h["source"] == "mac-table" else 1
        acc = 0 if h["access"] else 1
        return (src_rank, acc, deg.get(h["device_id"], 0))
    all_hits.sort(key=_rank)

    arp_refs = [h for h in learn_points if h["source"] == "arp"][:8]
    primary = all_hits[0] if all_hits else None
    return {"kind": "asset", "hits": all_hits[:50], "arp_refs": arp_refs,
            "primary_hit": primary["device_name"] if primary else None,
            "mac": target_mac}


def _group_arp(group: str) -> list[dict]:
    out: list[dict] = []
    for c in db.list_netdev_topology_cache(group):
        for a in c.get("arp") or []:
            out.append({"device_id": c["device_id"], "device_name":
                        (dev["name"] if (dev := db.get_netdev_device(c["device_id"])) else ""),
                        "ip": a.get("ip", ""), "mac": a.get("mac", ""), "port": a.get("port", "")})
    return out


async def topology_payload(group: str, force: bool = False) -> dict:
    stats = await collect_group(group, force=force)
    graph = build_graph(group)
    graph["collect_stats"] = stats
    graph["groups"] = sorted({d.get("group_name", "") for d in db.list_netdev_devices()})
    return graph
