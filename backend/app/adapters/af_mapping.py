"""AF 读侧映射：设备原生结构 → Agent 扁平字段（纯函数，从 af_rest 拆出）。"""
from app.adapters import af_write


def first(seq, default="any"):
    seq = seq or []
    return str(seq[0]) if isinstance(seq, list) and seq else (str(seq) if seq else default)


def join(seq, default="any"):
    seq = seq or []
    return ",".join(str(x) for x in seq) if isinstance(seq, list) and seq else (str(seq) if seq else default)


def acl_flat(r: dict) -> dict:
    """原生应用控制策略 → Agent 扁平字段（与 API 文档结构一致，供展示与变更比对）。"""
    src = r.get("src") or {}
    dst = r.get("dst") or {}
    src_addrs = src.get("srcAddrs") or {}
    dst_addrs = dst.get("dstAddrs") or {}
    action = r.get("action")
    # 实测 AF 8.0.45：0=拒绝（默认策略 Default Policy action=0 兜底拒绝），
    # 1=允许（名为 allow 的放行策略 action=1 持续命中）
    action_str = {0: "deny", 1: "allow"}.get(action, str(action))
    # lastHitTime 为 1970 表示从未命中
    never_hit = str(r.get("lastHitTime", "")).startswith("1970")
    return {
        "id": r.get("uuid", ""), "name": r.get("name", ""),
        "enabled": bool(r.get("enable", True)),
        "src_zone": join(src.get("srcZones"), "any"),
        "dst_zone": join(dst.get("dstZones"), "any"),
        "src_addr": join(src_addrs.get("srcIpGroups"), "全部"),
        "dst_addr": join(dst_addrs.get("dstIpGroups") or dst_addrs.get("srcIpGroups"),
                              "全部"),
        "service": join(dst.get("services"), "any"),
        "app": join(dst.get("applications"), "全部"),
        "action": action_str,
        "hit_count": 0 if never_hit else -1,
        "log": bool((r.get("advanceOption") or {}).get("logEnable", False)),
        "comment": r.get("description", ""),
    }


def nat_flat_of_raw(raw: dict) -> dict:
    """原生 NAT 策略 → 扁平字段（变更比对用，口径与 get_nat_rules 一致）。"""
    ntype = str(raw.get("natType", "SNAT")).upper()
    body = raw.get("bnat") if ntype == "BNAT" else (raw.get("dnat") or raw.get("snat"))
    body = body if isinstance(body, dict) else {}
    transfer = body.get("transfer") if isinstance(body.get("transfer"), dict) else {}
    translated = af_write.transfer_addr_str(transfer)
    port = af_write.transfer_port_str(transfer)
    if ntype == "DNAT":
        dst = first((body.get("dstIpobj") or {}).get("specifyIp"), "")
    else:
        dst = join(body.get("dstIpGroups"))
    return {
        "name": raw.get("name", ""),
        "enabled": bool(raw.get("enable", True)),
        "comment": raw.get("description", ""),
        "src_zone": first(body.get("srcZones"), "any"),
        "src_addr": join(body.get("srcIpGroups")),
        "service": join(body.get("natService") or body.get("services")),
        "dst_addr": dst or "any",
        "translated_addr": str(translated or ""),
        "translated_port": str(port or ""),
    }
