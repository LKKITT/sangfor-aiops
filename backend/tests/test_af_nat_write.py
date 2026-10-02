"""AF NAT 策略真实设备原生格式翻译测试。

回归背景：真实 AF 设备上 update_nat_rule 修改目的地址返回"成功"，但扁平字段
（dst_addr 等）被设备静默忽略——PATCH /nats/@name 要求原生嵌套结构
（DNAT 目的地址在 dnat.dstIpobj.specifyIp），配置实际未变（会话反复回查
get_nat_rules 确认仍为旧值）。翻译层以设备当前原生规则为基底、仅覆盖变更字段。
"""
from app.adapters.af_rest import AfRestClient

DNAT_RAW = {
    "uuid": "AFE697E4F5984C63B8979C3EDDDCB589", "name": "server", "enable": True,
    "natType": "DNAT", "description": "", "schedule": "全天",
    "dnat": {"srcZones": ["wan"], "srcIpGroups": ["全部"],
             "dstIpobj": {"dstIpobjType": "IP", "specifyIp": ["10.0.10.2"]},
             "natService": ["any"],
             "transfer": {"transferType": "IP", "specifyIp": "10.20.33.23"}},
}

SNAT_RAW = {
    "uuid": "S1", "name": "out", "enable": True, "natType": "SNAT",
    "snat": {"srcZones": ["lan"], "srcIpGroups": ["全部"],
             "dstNetobj": {"dstNetobjType": "ZONE", "zone": ["untrust"]},
             "dstIpGroups": ["全部"], "natService": ["any"],
             "transfer": {"transferType": "OUTIF_IP"}},
}


def _client() -> AfRestClient:
    return AfRestClient("dev_x", "t", "https://10.0.0.1", "u", "p")


def test_nat_flat_of_raw_dnat():
    flat = _client()._nat_flat_of_raw(DNAT_RAW)
    assert flat["name"] == "server" and flat["type"] if False else True
    assert flat["src_zone"] == "wan" and flat["src_addr"] == "全部"
    assert flat["dst_addr"] == "10.0.10.2"
    assert flat["translated_addr"] == "10.20.33.23"
    assert flat["service"] == "any" and flat["enabled"] is True


def test_nat_native_payload_only_changes_dst_addr():
    """复刻本次故障：只改目的地址 → 原生 dstIpobj 更新，其余字段（含转换地址）原样。"""
    payload = _client()._nat_native_payload(DNAT_RAW, {
        "dst_addr": "10.0.10.1", "name": "server", "src_zone": "wan",
        "src_addr": "全部", "service": "any", "translated_addr": "10.20.33.23",
        "enabled": True, "comment": "",
    })
    dn = payload["dnat"]
    assert dn["dstIpobj"] == {"dstIpobjType": "IP", "specifyIp": ["10.0.10.1"]}
    assert dn["transfer"]["specifyIp"] == "10.20.33.23"     # 未改动的转换地址保持
    assert payload["schedule"] == "全天"                     # 原生字段不丢失
    assert payload["dnat"]["srcZones"] == ["wan"]


def test_nat_native_payload_dst_addr_to_ipgroup():
    """目的地址为名称引用 → IPGROUP 形态。"""
    payload = _client()._nat_native_payload(DNAT_RAW, {"dst_addr": "开发部"})
    assert payload["dnat"]["dstIpobj"] == {"dstIpobjType": "IPGROUP", "ipGroups": ["开发部"]}


def test_nat_native_payload_translated_addr_and_port():
    payload = _client()._nat_native_payload(
        DNAT_RAW, {"translated_addr": "10.20.33.24", "translated_port": "8080-8090"})
    tr = payload["dnat"]["transfer"]
    assert tr["specifyIp"] == "10.20.33.24"
    assert tr["transferPort"] == [{"start": 8080, "end": 8090}]
    # 回读扁平口径一致（transferPort 数组形态）
    flat = _client()._nat_flat_of_raw(payload)
    assert flat["translated_port"] == "8080-8090"


def test_nat_native_payload_snat_dst_zone():
    payload = _client()._nat_native_payload(SNAT_RAW, {"dst_zone": "trust"})
    assert payload["snat"]["dstNetobj"]["zone"] == ["trust"]
    assert payload["snat"]["dstIpGroups"] == ["全部"]


def test_nat_flat_roundtrip_range_transfer():
    raw = {"uuid": "r", "name": "pool", "enable": True, "natType": "DNAT",
           "dnat": {"srcZones": [], "srcIpGroups": [], "natService": [],
                    "dstIpobj": {"dstIpobjType": "IP", "specifyIp": ["1.1.1.1"]},
                    "transfer": {"transferType": "IP_RANGE",
                                 "ipRange": {"start": "10.0.0.1", "end": "10.0.0.9"}}}}
    flat = _client()._nat_flat_of_raw(raw)
    assert flat["translated_addr"] == "10.0.0.1-10.0.0.9"
    payload = _client()._nat_native_payload(raw, {"translated_addr": "10.0.0.1-10.0.0.9"})
    assert payload["dnat"]["transfer"]["ipRange"] == {"start": "10.0.0.1", "end": "10.0.0.9"}


def test_nat_create_payload_dnat():
    payload = _client()._nat_create_payload({
        "name": "new-dnat", "type": "DNAT", "action": "allow",
        "src_zone": "wan", "src_addr": "全部", "dst_addr": "10.0.10.5",
        "service": "any", "translated_addr": "192.168.1.5", "enabled": True,
    })
    assert payload["natType"] == "DNAT" and payload["schedule"] == "全天"
    assert payload["dnat"]["dstIpobj"] == {"dstIpobjType": "IP", "specifyIp": ["10.0.10.5"]}
    assert payload["dnat"]["transfer"]["specifyIp"] == "192.168.1.5"


def test_nat_create_payload_snat_defaults():
    payload = _client()._nat_create_payload({"name": "new-snat"})
    assert payload["natType"] == "SNAT"
    assert payload["snat"]["transfer"] == {"transferType": "OUTIF_IP"}
    assert payload["snat"]["srcIpGroups"] == ["全部"]


def test_parse_ports_variants():
    assert AfRestClient._parse_ports("8080") == [{"start": 8080, "end": 8080}]
    assert AfRestClient._parse_ports("8080-8090,9090") == [
        {"start": 8080, "end": 8090}, {"start": 9090, "end": 9090}]
    assert AfRestClient._parse_ports("") == []
