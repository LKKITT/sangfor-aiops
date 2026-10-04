"""AF 写格式翻译：Agent 扁平字段 → 设备原生载荷（纯函数，从 af_rest 拆出）。

读侧映射见 af_mapping；需要设备交互的引用自动创建（_ensure_*）与 HTTP 编排
保留在 AfRestClient。本模块全部为纯函数，可绕开 mock 设备直测。
"""
import re


def ip_ranges(members: str) -> list[dict]:
    """'10.1.1.0/24,192.168.1.5,10.0.0.1-10.0.0.10' → ipRanges [{start,end}]。"""
    import ipaddress
    ranges = []
    for part in (members or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part and not part.count(":"):
                start, end = [p.strip() for p in part.split("-", 1)]
                ipaddress.ip_address(start)
                ipaddress.ip_address(end)
                ranges.append({"start": start, "end": end})
            else:
                net = ipaddress.ip_network(part, strict=False)
                ranges.append({"start": str(net.network_address),
                               "end": str(net.broadcast_address)})
        except ValueError:
            continue
    return ranges


def service_payload(data: dict) -> dict:
    """{name, protocol, ports, comment} → 设备原生 servType/tcpEntrys/udpEntrys。"""
    import re as _re
    protocol = str(data.get("protocol", "TCP")).upper()
    payload = {"name": data.get("name", ""), "servType": "USRDEF_SERV",
               "description": data.get("comment", data.get("description", ""))}
    dst_ports = []
    for part in _re.split(r"[，,]", str(data.get("ports", "") or "")):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            s, e = part.split("-", 1)
            dst_ports.append({"start": int(s), "end": int(e)})
        else:
            dst_ports.append({"start": int(part), "end": int(part)})
    entry = {"srcPorts": [{"start": 0, "end": 65535}],
             "dstPorts": dst_ports or [{"start": 0, "end": 65535}]}
    if "UDP" in protocol and "TCP" not in protocol:
        payload["udpEntrys"] = [entry]
    else:
        payload["tcpEntrys"] = [entry]
        if "UDP" in protocol:
            payload["udpEntrys"] = [entry]
    return payload


def ip_like(part: str) -> bool:
    """是否为可自动创建为 IP组 的形态：IP / CIDR / IP-IP 范围。"""
    import ipaddress
    part = part.strip()
    try:
        ipaddress.ip_network(part, strict=False)
        return True
    except ValueError:
        pass
    if "-" in part:
        try:
            start, end = part.split("-", 1)
            ipaddress.ip_address(start.strip())
            ipaddress.ip_address(end.strip())
            return True
        except ValueError:
            pass
    return False


def parse_port_spec(part: str):
    """解析端口形态的服务引用：'TCP5211'/'tcp/5211'/'5211'/'UDP 53'/'5211-5220'
    → (协议大写, 端口串)；非端口形态返回 None。无协议前缀时默认 TCP。"""
    import re as _re
    m = _re.fullmatch(r"(?:(tcp|udp)[\s/:_-]*)?(\d{1,5})(?:\s*-\s*(\d{1,5}))?",
                      str(part).strip(), _re.IGNORECASE)
    if not m:
        return None
    proto = (m.group(1) or "tcp").upper()
    start, end = m.group(2), m.group(3) or m.group(2)
    return proto, (start if start == end else f"{start}-{end}")


def acl_action_int(value) -> int:
    """动作 → 设备原生 uint32（API 文档：0=拒绝，1=允许）。"""
    if isinstance(value, int):
        return 1 if value >= 1 else 0
    return 1 if str(value).strip().lower() in ("allow", "permit", "accept", "1") else 0


def acl_native_payload(raw: dict, data: dict) -> dict:
    """把发生变化的扁平字段翻译后合并到设备原生策略结构（update 用）。

    以设备当前原生规则为基底，仅覆盖与当前扁平值不同的字段——未修改字段保持
    设备原样，避免展示值（如"全部"）无法逆翻译、或类型偏差触发"参数类型不匹配"。
    """
    from app.adapters import af_mapping   # 惰性导入：af_mapping 顶层依赖本模块
    import copy as _copy
    payload = _copy.deepcopy(raw)
    cur = af_mapping.acl_flat(raw)
    src = payload["src"] if isinstance(payload.get("src"), dict) else {}
    dst = payload["dst"] if isinstance(payload.get("dst"), dict) else {}
    src_addrs = src["srcAddrs"] if isinstance(src.get("srcAddrs"), dict) else {}
    dst_addrs = dst["dstAddrs"] if isinstance(dst.get("dstAddrs"), dict) else {}
    advance = payload["advanceOption"] if isinstance(payload.get("advanceOption"), dict) else {}

    def split_list(value) -> list[str]:
        return [p.strip() for p in str(value).replace("，", ",").split(",") if p.strip()]

    def changed(key) -> bool:
        return key in data and str(data.get(key)) != str(cur.get(key))

    if changed("name"):
        payload["name"] = data["name"]
    if changed("enabled"):
        payload["enable"] = bool(data["enabled"])
    if changed("comment"):
        payload["description"] = data.get("comment", "")
    if changed("action"):
        payload["action"] = acl_action_int(data["action"])
    if changed("log"):
        advance["logEnable"] = bool(data["log"])
    if changed("src_zone"):
        src["srcZones"] = split_list(data["src_zone"])
    if changed("dst_zone"):
        dst["dstZones"] = split_list(data["dst_zone"])
    if changed("src_addr"):
        src_addrs.setdefault("srcAddrType", "NETOBJECT")
        src_addrs["srcIpGroups"] = split_list(data["src_addr"])
    if changed("dst_addr"):
        dst_addrs.setdefault("dstAddrType", "NETOBJECT")
        dst_addrs["dstIpGroups"] = split_list(data["dst_addr"])
    if changed("service"):
        dst["services"] = split_list(data["service"])
    if changed("app"):
        dst["applications"] = split_list(data["app"])
    payload["src"], payload["dst"], payload["advanceOption"] = src, dst, advance
    src["srcAddrs"], dst["dstAddrs"] = src_addrs, dst_addrs
    return payload


def acl_create_payload(data: dict) -> dict:
    """扁平字段 → 原生应用控制策略完整载荷（create 用，结构按 API 文档样例）。"""

    def split_list(value) -> list[str]:
        return [p.strip() for p in str(value or "").replace("，", ",").split(",") if p.strip()]

    return {
        "name": str(data.get("name", "")),
        "enable": bool(data.get("enabled", True)),
        "action": acl_action_int(data.get("action", "allow")),
        "description": str(data.get("comment", "")),
        "schedule": "全天",
        "group": "默认策略组",
        "labels": {},
        "src": {"srcZones": split_list(data.get("src_zone")) or {},
                "srcAddrs": {"srcAddrType": "NETOBJECT",
                             "srcIpGroups": split_list(data.get("src_addr")) or ["全部"]}},
        "dst": {"dstZones": split_list(data.get("dst_zone")) or {},
                "dstAddrs": {"dstAddrType": "NETOBJECT",
                             "dstIpGroups": split_list(data.get("dst_addr")) or ["全部"]},
                "services": split_list(data.get("service")) or ["any"],
                "applications": split_list(data.get("app")) or ["全部"]},
        "advanceOption": {"logEnable": bool(data.get("log", False))},
    }


def transfer_addr_str(transfer: dict) -> str:
    """原生 transfer → 转换地址串（兼容 specifyIp 列表/单值与 ipRange 范围）。"""
    translated = transfer.get("specifyIp", "")
    if isinstance(translated, list):
        translated = ",".join(str(x) for x in translated)
    if not translated and isinstance(transfer.get("ipRange"), dict):
        rg = transfer["ipRange"]
        translated = f"{rg.get('start', '')}-{rg.get('end', '')}"
    return str(translated or "")


def transfer_port_str(transfer: dict) -> str:
    """原生 transfer → 端口串（兼容 specifyPort/port 旧形态与 transferPort 数组）。"""
    port = transfer.get("specifyPort") or transfer.get("port") or ""
    tports = transfer.get("transferPort") or []
    if not port and tports:
        parts = []
        for tp in tports:
            if isinstance(tp, dict):
                start, end = tp.get("start"), tp.get("end")
                parts.append(str(start) if start == end else f"{start}-{end}")
            else:
                parts.append(str(tp))
        port = ",".join(parts)
    return str(port or "")


def split_refs(value) -> list[str]:
    return [p.strip() for p in str(value or "").replace("，", ",").split(",") if p.strip()]


def dst_ipobj(value) -> dict:
    """扁平 dst_addr → 原生 dstIpobj：全为 IP/网段 → IP 列表；否则按 IP组名引用。"""
    parts = split_refs(value)
    if parts and all(ip_like(x) for x in parts):
        return {"dstIpobjType": "IP", "specifyIp": parts}
    return {"dstIpobjType": "IPGROUP", "ipGroups": parts}


def parse_ports(value) -> list[dict]:
    """'8080' / '8080-8090,9090' → transferPort [{start,end}]。"""
    ports = []
    for part in split_refs(value):
        m = re.fullmatch(r"(\d{1,5})(?:\s*-\s*(\d{1,5}))?", part)
        if not m:
            continue
        start, end = int(m.group(1)), int(m.group(2) or m.group(1))
        ports.append({"start": start, "end": end})
    return ports


def apply_transfer(transfer: dict, data: dict, cur: dict) -> dict:
    """把 translated_addr/translated_port 的变更合并进原生 transfer 结构。"""
    transfer = transfer if isinstance(transfer, dict) else {}
    if "translated_addr" in data and str(data["translated_addr"]) != str(cur.get("translated_addr")):
        val = str(data["translated_addr"]).strip()
        if "-" in val and not val.count(":"):
            start, _, end = val.partition("-")
            transfer.update({"transferType": "IP_RANGE",
                             "ipRange": {"start": start.strip(), "end": end.strip()}})
            transfer.pop("specifyIp", None)
            transfer.pop("ipGroups", None)
        elif ip_like(val):
            transfer.update({"transferType": "IP", "specifyIp": val})
            transfer.pop("ipRange", None)
            transfer.pop("ipGroups", None)
        elif val:
            transfer.update({"transferType": "IPGROUP", "ipGroups": split_refs(val)})
            transfer.pop("specifyIp", None)
            transfer.pop("ipRange", None)
    if "translated_port" in data and str(data["translated_port"]) != str(cur.get("translated_port")):
        transfer["transferPort"] = parse_ports(data["translated_port"])
    return transfer


def nat_native_payload(raw: dict, data: dict) -> dict:
    """把发生变化的扁平字段翻译后合并到设备原生 NAT 结构（update 用）。

    以设备当前原生规则为基底，仅覆盖变更字段；未识别字段保持设备原样。
    BNAT（双向 NAT）结构复杂，仅翻译名称/启停/备注等通用字段。
    """
    from app.adapters import af_mapping   # 惰性导入：af_mapping 顶层依赖本模块
    import copy as _copy
    payload = _copy.deepcopy(raw)
    cur = af_mapping.nat_flat_of_raw(raw)
    ntype = str(raw.get("natType", "SNAT")).upper()

    def changed(key) -> bool:
        return key in data and str(data[key]) != str(cur.get(key))

    if changed("name"):
        payload["name"] = data["name"]
    if changed("enabled"):
        payload["enable"] = bool(data["enabled"])
    if changed("comment"):
        payload["description"] = data.get("comment", "")
    if ntype == "BNAT":
        return payload

    body_key = "dnat" if ntype == "DNAT" else "snat"
    body = payload.get(body_key) if isinstance(payload.get(body_key), dict) else {}
    payload[body_key] = body

    if changed("src_zone"):
        body["srcZones"] = split_refs(data["src_zone"])
    if changed("src_addr"):
        body["srcIpGroups"] = split_refs(data["src_addr"])
    if changed("service"):
        body["natService"] = split_refs(data["service"])
    if ntype == "DNAT":
        if changed("dst_addr"):
            body["dstIpobj"] = dst_ipobj(data["dst_addr"])
    else:
        if changed("dst_zone"):
            netobj = body.get("dstNetobj") if isinstance(body.get("dstNetobj"), dict) else {}
            netobj.setdefault("dstNetobjType", "ZONE")
            netobj["zone"] = split_refs(data["dst_zone"])
            body["dstNetobj"] = netobj
        if changed("dst_addr"):
            body["dstIpGroups"] = split_refs(data["dst_addr"])
    if changed("translated_addr") or changed("translated_port"):
        body["transfer"] = apply_transfer(body.get("transfer") or {}, data, cur)
    return payload


def nat_create_payload(data: dict) -> dict:
    """扁平字段 → 原生 NAT 完整载荷（create 用，结构按 API 文档 5.1）。"""
    ntype = str(data.get("type", "SNAT") or "SNAT").upper()
    payload = {
        "name": str(data.get("name", "")),
        "enable": bool(data.get("enabled", True)),
        "natType": ntype,
        "description": str(data.get("comment", "")),
        "schedule": "全天",
    }
    transfer = apply_transfer({}, {"translated_addr": data.get("translated_addr", ""),
                                        "translated_port": data.get("translated_port", "")},
                                   {"translated_addr": "", "translated_port": ""})
    if ntype == "DNAT":
        payload["dnat"] = {
            "srcZones": split_refs(data.get("src_zone")) or ["any"],
            "srcIpGroups": split_refs(data.get("src_addr")) or ["全部"],
            "dstIpobj": dst_ipobj(data.get("dst_addr", "any")),
            "natService": split_refs(data.get("service")) or ["any"],
            "transfer": transfer or {"transferType": "NO_TRANS"},
        }
    else:
        payload["snat"] = {
            "srcZones": split_refs(data.get("src_zone")) or ["any"],
            "srcIpGroups": split_refs(data.get("src_addr")) or ["全部"],
            "dstNetobj": {"dstNetobjType": "ZONE",
                          "zone": split_refs(data.get("dst_zone")) or ["any"]},
            "dstIpGroups": split_refs(data.get("dst_addr")) or ["全部"],
            "natService": split_refs(data.get("service")) or ["any"],
            "transfer": transfer or {"transferType": "OUTIF_IP"},
        }
    return payload
