"""AF 应用控制策略（ACL）真实设备原生格式翻译测试。

回归背景：真实 AF 设备上 update_acl_rule 把扁平字段（action:"allow" 字符串等）直接
PATCH，设备返回「应用控制策略[策略动作]：参数类型不匹配」。翻译层必须以设备原生
规则为基底、只覆盖用户变更的字段，且 action 翻译为 0/1 整数。
"""
from app.adapters.af_rest import AfRestClient

# 设备原生结构（按 API 文档 5.2 访问控制样例构造，对齐 8.0.45 实测）
RAW_RULE = {
    "uuid": "BA2EE0C51CF04238B10C2EE8E1014773",
    "name": "smb",
    "enable": True,
    "action": 0,
    "src": {"srcZones": {},
            "srcAddrs": {"srcAddrType": "NETOBJECT", "srcIpGroups": ["全部"]}},
    "dst": {"dstZones": {},
            "dstAddrs": {"dstAddrType": "NETOBJECT", "dstIpGroups": ["全部"]},
            "applications": ["全部"], "services": ["smb"]},
    "advanceOption": {"autoSynDNS": False, "logEnable": False, "keepAlive": 0},
    "description": "",
    "schedule": "全天",
    "group": "默认策略组",
}


def test_acl_flat_reads_native_fields():
    flat = AfRestClient._acl_flat(RAW_RULE)
    assert flat["name"] == "smb" and flat["action"] == "deny"
    assert flat["log"] is False and flat["enabled"] is True
    assert flat["service"] == "smb" and flat["src_addr"] == "全部"
    assert flat["src_zone"] == "any"   # 空 srcZones（{}）展示为 any


def test_acl_native_payload_only_changes_requested_fields():
    """只改日志：action/地址/服务等保持设备原值，且原生字段（schedule/group）不丢失。

    当前版编排器只把用户变更的字段放进 data（如 {"log": true}），不会混入全量快照；
    data 中出现与设备不同的字段即视为用户变更并翻译应用（见下方 action 用例）。
    """
    payload = AfRestClient._acl_native_payload(RAW_RULE, {"log": True})
    assert payload["advanceOption"]["logEnable"] is True          # 用户要改的字段生效
    assert payload["action"] == 0                                 # 未改动的 action 保持原值（不再发 "allow" 字符串）
    assert payload["dst"]["dstAddrs"]["dstIpGroups"] == ["全部"]
    assert payload["src"]["srcAddrs"]["srcAddrType"] == "NETOBJECT"
    assert payload["schedule"] == "全天" and payload["group"] == "默认策略组"


def test_acl_native_payload_action_flip_and_disable():
    payload = AfRestClient._acl_native_payload(RAW_RULE, {"action": "allow"})
    assert payload["action"] == 1 and payload["advanceOption"]["logEnable"] is False
    payload2 = AfRestClient._acl_native_payload(RAW_RULE, {"enabled": False, "comment": "临时停用"})
    assert payload2["enable"] is False and payload2["description"] == "临时停用"
    payload3 = AfRestClient._acl_native_payload(RAW_RULE, {"service": "https,443"})
    assert payload3["dst"]["services"] == ["https", "443"]


def test_acl_action_int():
    assert AfRestClient._acl_action_int("allow") == 1
    assert AfRestClient._acl_action_int("deny") == 0
    assert AfRestClient._acl_action_int(1) == 1
    assert AfRestClient._acl_action_int("1") == 1
    assert AfRestClient._acl_action_int("") == 0


def test_acl_create_payload_native_types():
    payload = AfRestClient._acl_create_payload({
        "name": "web-pass", "action": "deny", "log": True,
        "src_zone": "lan,wan", "service": "https",
    })
    assert payload["action"] == 0 and payload["enable"] is True
    assert payload["advanceOption"]["logEnable"] is True
    assert payload["src"]["srcZones"] == ["lan", "wan"]
    assert payload["dst"]["services"] == ["https"]
    assert payload["src"]["srcAddrs"]["srcAddrType"] == "NETOBJECT"
    assert payload["schedule"] == "全天"          # 创建必填的生效时间计划


# ---------- 引用 IP组 不存在时自动创建 ----------

import pytest

from app.adapters.base import NetworkObject, ServiceConfig
from app.adapters.base import DeviceError


def test_ip_like():
    assert AfRestClient._ip_like("192.168.1.10")
    assert AfRestClient._ip_like("10.0.0.0/24")
    assert AfRestClient._ip_like("1.1.1.1-1.1.1.5")
    assert not AfRestClient._ip_like("总部IP组")
    assert not AfRestClient._ip_like("全部")
    assert not AfRestClient._ip_like("")


@pytest.mark.asyncio
async def test_ensure_acl_addr_groups_creates_missing_ip_object():
    """裸 IP/网段不存在 → 自动创建同名网络对象；已存在的组与名称引用不动。"""
    c = AfRestClient("dev_x", "t", "https://10.0.0.1", "u", "p")
    created = []

    async def fake_apply(change):
        created.append(change)
        return {"ok": True}

    async def fake_objects():
        return [NetworkObject(id="1", name="总部IP组", type="ipgroup", members="10.0.0.0/8")]

    c.apply_change = fake_apply
    c.get_network_objects = fake_objects

    data = {"dst_addr": "192.168.1.10,总部IP组", "src_addr": "全部"}
    await c._ensure_acl_addr_groups(data)
    assert len(created) == 1
    assert created[0].op == "create" and created[0].resource == "object"
    assert created[0].data["name"] == "192.168.1.10"
    assert created[0].data["members"] == "192.168.1.10"
    assert data["dst_addr"] == "192.168.1.10,总部IP组"   # 同名创建，引用无需改写
    assert data["src_addr"] == "全部"                     # 内置"全部"不代建

    # CIDR：建组名"/"替换为"_"，并同步改写引用值
    data2 = {"dst_addr": "10.0.0.0/24"}
    await c._ensure_acl_addr_groups(data2)
    assert created[1].data["name"] == "10.0.0.0_24"
    assert created[1].data["members"] == "10.0.0.0/24"
    assert data2["dst_addr"] == "10.0.0.0_24"

    # 已存在的名称引用：不重复创建
    data3 = {"dst_addr": "总部IP组"}
    await c._ensure_acl_addr_groups(data3)
    assert len(created) == 2


@pytest.mark.asyncio
async def test_acl_missing_refs_note_prefills_plan_warning():
    """变更计划预告：引用的 IP组/服务 不存在时提示确认后自动创建（或需人工调整）。"""
    from app.agent.tools import _acl_missing_refs_note

    class _FakeClient:
        async def get_network_objects(self):
            return [NetworkObject(id="1", name="总部IP组", type="ipgroup", members="10.0.0.0/8")]

        async def get_services(self):
            return [ServiceConfig(id="s1", name="TCP_5211", protocol="TCP", ports="5211")]

        async def get_predefined_services(self):
            return [ServiceConfig(id="p1", name="ftp", protocol="TCP", ports="21")]

    note = await _acl_missing_refs_note(_FakeClient(), {"dst_addr": "192.168.1.10"})
    assert "192.168.1.10" in note and "自动创建同名网络对象" in note
    # 引用都已存在：无提示
    note2 = await _acl_missing_refs_note(
        _FakeClient(), {"dst_addr": "总部IP组", "src_addr": "全部", "service": "ftp,TCP_5211"})
    assert note2 == ""
    # 端口形态服务不存在：预告自动创建
    note3 = await _acl_missing_refs_note(_FakeClient(), {"service": "TCP5311"})
    assert "TCP5311" in note3 and "自动创建自定义服务" in note3
    # 纯名称服务不存在：明确提示需人工调整
    note4 = await _acl_missing_refs_note(_FakeClient(), {"service": "mail"})
    assert "mail" in note4 and "均不存在" in note4


# ---------- 地址字段 any → 「全部」规范化（回归：any 引用被设备拒绝） ----------

ACL_FLAT_RULE = {"id": "BA2EE0C51CF04238B10C2EE8E1014773", "name": "smb", "enabled": True,
                 "src_zone": "any", "dst_zone": "any", "src_addr": "全部", "dst_addr": "全部",
                 "service": "smb", "app": "全部", "action": "deny", "hit_count": 0,
                 "log": False, "comment": ""}


@pytest.mark.asyncio
async def test_ensure_acl_addr_groups_normalizes_any():
    """地址字段 any/任意 规范化为内置网络对象「全部」，不再作为不存在的引用下发。"""
    c = AfRestClient("dev_x", "t", "https://10.0.0.1", "u", "p")
    created = []

    async def fake_apply(change):
        created.append(change)
        return {"ok": True}

    async def fake_objects():
        return [NetworkObject(id="1", name="全部", type="ipgroup",
                              members="0.0.0.0-255.255.255.255")]

    c.apply_change = fake_apply
    c.get_network_objects = fake_objects
    data = {"dst_addr": "any", "src_addr": "任意"}
    await c._ensure_acl_addr_groups(data)
    assert data == {"dst_addr": "全部", "src_addr": "全部"}
    assert created == []          # 「全部」已存在，无需建组


@pytest.mark.asyncio
async def test_prepare_rule_change_normalizes_any_addr():
    """变更计划与下发数据同步规范化：卡片展示「全部」，执行数据不再含字面量 any。"""
    from app.agent.tools import _prepare_rule_change

    class _FakeClient:
        async def snapshot_config(self):
            return {"acl_rules": [dict(ACL_FLAT_RULE)]}

        async def get_network_objects(self):
            return [NetworkObject(id="1", name="全部", type="ipgroup",
                                  members="0.0.0.0-255.255.255.255")]

    args = {"_resource": "acl", "_op": "update",
            "data": {"dst_addr": "any", "comment": "恢复全局拒绝SMB"},
            "rule_id": "BA2EE0C51CF04238B10C2EE8E1014773"}
    device = {"id": "dev_x", "name": "10.20.33.20", "type": "af", "mode": "real"}
    plan = await _prepare_rule_change(_FakeClient(), args, device)
    assert plan["after"]["dst_addr"] == "全部"
    assert args["data"]["dst_addr"] == "全部"        # 执行数据（待确认动作）同步规范化
    assert plan["before"]["dst_addr"] == "全部"
    assert "自动创建" not in plan["warning"]          # 「全部」已存在，无建组预告


# ---------- 引用服务不存在：自定义 → 预定义 → 端口形态自动建（回归：服务改 ftp 无从核实） ----------

def test_parse_port_spec():
    assert AfRestClient._parse_port_spec("TCP5211") == ("TCP", "5211")
    assert AfRestClient._parse_port_spec("tcp/5211") == ("TCP", "5211")
    assert AfRestClient._parse_port_spec("5211") == ("TCP", "5211")
    assert AfRestClient._parse_port_spec("UDP 53") == ("UDP", "53")
    assert AfRestClient._parse_port_spec("5211-5220") == ("TCP", "5211-5220")
    assert AfRestClient._parse_port_spec("smb") is None
    assert AfRestClient._parse_port_spec("TCP") is None
    assert AfRestClient._parse_port_spec("") is None


class _FakeSvcClient:
    """服务自动建组的假客户端：记录 create 调用并可控制预定义列表可用性。"""

    def __init__(self, custom, predefined=None):
        self._custom = custom
        self._predefined = predefined
        self.created = []

    async def get_services(self):
        return self._custom

    async def get_predefined_services(self):
        if self._predefined is None:
            raise RuntimeError("预定义列表不可用")
        return self._predefined

    async def apply_change(self, change):
        self.created.append(change)
        return {"ok": True}


@pytest.mark.asyncio
async def test_ensure_acl_services_creates_port_spec_service():
    """端口形态（TCP5211）不在自定义/预定义中 → 创建自定义服务并改写引用。"""
    c = _FakeSvcClient(custom=[], predefined=[ServiceConfig(id="p", name="ftp", protocol="TCP", ports="21")])
    data = {"service": "ftp,TCP5211"}
    await AfRestClient._ensure_acl_services(c, data)
    assert len(c.created) == 1
    assert c.created[0].resource == "service"
    assert c.created[0].data == {"name": "TCP_5211", "protocol": "TCP", "ports": "5211",
                                 "comment": "访问控制策略引用，由 Agent 自动创建"}
    assert data["service"] == "ftp,TCP_5211"      # ftp 为预定义服务直接引用


@pytest.mark.asyncio
async def test_ensure_acl_services_reuses_same_ports():
    """同协议同端口的自定义服务已存在（即便名称不同）→ 复用不重建。"""
    c = _FakeSvcClient(custom=[ServiceConfig(id="s", name="旧5211", protocol="TCP", ports="5211")],
                       predefined=[])
    data = {"service": "TCP5211"}
    await AfRestClient._ensure_acl_services(c, data)
    assert c.created == [] and data["service"] == "旧5211"


@pytest.mark.asyncio
async def test_ensure_acl_services_plain_name_not_found_raises():
    """纯名称在自定义/预定义均不存在：明确报错指引（无端口信息无法代建）。"""
    c = _FakeSvcClient(custom=[ServiceConfig(id="s", name="smb", protocol="TCP", ports="445")],
                       predefined=[ServiceConfig(id="p", name="ftp", protocol="TCP", ports="21")])
    data = {"service": "mail"}
    try:
        await AfRestClient._ensure_acl_services(c, data)
        raised = False
    except DeviceError as e:
        raised = True
        assert "mail" in str(e) and "TCP5211" in str(e)
    assert raised and c.created == []


@pytest.mark.asyncio
async def test_ensure_acl_services_predefined_unavailable_passes_through():
    """预定义列表不可用时：纯名称保留原值交由设备校验（避免误拒预定义服务）。"""
    c = _FakeSvcClient(custom=[ServiceConfig(id="s", name="smb", protocol="TCP", ports="445")],
                       predefined=None)
    data = {"service": "ftp"}
    await AfRestClient._ensure_acl_services(c, data)
    assert data["service"] == "ftp" and c.created == []
