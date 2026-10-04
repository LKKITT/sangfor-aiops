"""网络设备 AI 对话（netdev_* 工具）与全局助手测试：
工具注册与上下文过滤、厂家白名单、命令目录、配置下发确认计划、终端定位、
编排器 devices 批量 fan-out、全局模式默认态、演示设备下线后的 API 行为。
"""
import asyncio

import pytest

from app import db
from app.agent import guardrails, skills
from app.agent.netdev_tools import (_full_commands, _h_get_status, _h_locate_terminal, _p_apply_config, _resolve_targets,
                                    _vendor_check)
from app.agent.orchestrator import (AgentOrchestrator, device_context_type,
                                    load_any_device, match_sangfor_devices,
                                    resolve_batch_targets, _global_target_guidance)
from app.agent.tools import TOOLS_BY_NAME, get_tools, get_tools_by_name
from app.services import netdev_service, netdev_topology_service as topo


@pytest.fixture()
def nd_h3c():
    rec = db.save_netdev_device({"name": "AI核心交换机", "vendor": "h3c", "host": "10.77.0.1",
                                 "port": 22, "username": "admin", "password": "H3C@123",
                                 "group_name": "AI测试组"})
    yield rec
    db.delete_netdev_device(rec["id"])


@pytest.fixture()
def nd_huawei():
    rec = db.save_netdev_device({"name": "AI接入交换机", "vendor": "huawei", "host": "10.77.0.2",
                                 "port": 22, "username": "admin", "password": "HW@123"})
    yield rec
    db.delete_netdev_device(rec["id"])


@pytest.fixture()
def nd_cisco():
    rec = db.save_netdev_device({"name": "AI思科设备", "vendor": "cisco", "host": "10.77.0.3",
                                 "port": 22, "username": "admin", "password": "x"})
    yield rec
    db.delete_netdev_device(rec["id"])


@pytest.fixture()
def fake_run(monkeypatch):
    """替换 run_commands：记录调用并返回固定输出。"""
    calls: list[tuple[str, list[str]]] = []

    async def _run(device, commands, timeout=30, **kw):
        calls.append((device["name"], list(commands)))
        return {"ok": True, "output": f"<{device['name']}>回显", "duration": 0.1}

    monkeypatch.setattr(netdev_service, "run_commands", _run)
    return calls


# ---------- 工具注册与上下文过滤 ----------

def test_netdev_tools_registered():
    for name in ("netdev_list_devices", "netdev_get_status", "netdev_get_config",
                 "netdev_get_interfaces", "netdev_get_routes", "netdev_get_arp",
                 "netdev_get_logs", "netdev_locate_terminal", "netdev_apply_config",
                 "netdev_run_commands"):
        assert name in TOOLS_BY_NAME, name
        assert TOOLS_BY_NAME[name].device_type == "netdev"


def test_tool_scope_by_context():
    """netdev 上下文：网络设备工具 + 免连接通用工具；不含深信服设备类查询工具。"""
    names = {t.name for t in get_tools("netdev")}
    assert "netdev_get_status" in names and "netdev_apply_config" in names
    assert "search_official_knowledge" in names and "list_available_devices" in names
    assert "get_device_status" not in names and "get_nat_rules" not in names
    assert "create_acl_rule" not in names

    # 深信服上下文：原有工具全保留 + 网络设备工具（全局助手跨域）
    names_af = {t.name for t in get_tools("af")}
    assert "get_nat_rules" in names_af and "netdev_get_status" in names_af


def test_schema_devices_param_injection():
    """设备类工具 schema 自动注入 devices 批量参数；免连接工具不注入。"""
    status_schema = TOOLS_BY_NAME["get_device_status"].schema()
    assert "devices" in status_schema["function"]["parameters"]["properties"]
    kb_schema = TOOLS_BY_NAME["record_to_kb"].schema()
    assert "devices" not in kb_schema["function"]["parameters"]["properties"]
    # netdev 工具自带 devices 描述，注入不覆盖
    nd_schema = TOOLS_BY_NAME["netdev_apply_config"].schema()
    assert "目标网络设备列表" in nd_schema["function"]["parameters"]["properties"]["devices"]["description"]
    # 不支持批量的 netdev 工具不注入
    locate_schema = TOOLS_BY_NAME["netdev_locate_terminal"].schema()
    assert "devices" not in locate_schema["function"]["parameters"]["properties"]


# ---------- 设备解析与厂家白名单 ----------

def test_resolve_targets_by_devices_arg(nd_h3c, nd_huawei):
    devs, err = _resolve_targets({"devices": ["AI核心交换机", "10.77.0.2"]}, {})
    assert err == "" and [d["id"] for d in devs] == [nd_h3c["id"], nd_huawei["id"]]
    # 唯一子串匹配
    devs, err = _resolve_targets({"devices": ["AI核心"]}, {})
    assert err == "" and len(devs) == 1
    # 绑定网络设备时缺省对绑定设备执行
    devs, err = _resolve_targets({}, nd_h3c)
    assert err == "" and devs[0]["id"] == nd_h3c["id"]
    # 绑定深信服设备且未指定 → 要求显式指定
    devs, err = _resolve_targets({}, {"id": "dev_x", "type": "af"})
    assert devs is None and "请指定目标网络设备" in err


def test_vendor_whitelist(nd_cisco):
    devs, err = _resolve_targets({"devices": ["AI思科设备"]}, {})
    assert err == ""
    msg = _vendor_check(devs)
    assert "仅支持华为、H3C、锐捷" in msg and "AI思科设备" in msg
    ok = _vendor_check([{"name": "x", "vendor": "ruijie"}])
    assert ok == ""


# ---------- 命令目录与执行 ----------

def test_health_commands_per_vendor(nd_h3c, fake_run):
    result = asyncio.run(_h_get_status(None, {"devices": ["AI核心交换机"]}, {}))
    assert result["succeeded"] == 1
    name, cmds = fake_run[0]
    assert name == "AI核心交换机"
    assert cmds[0] == "display version" and "display cpu-usage" in cmds


def test_batch_query_merges_results(nd_h3c, nd_huawei, fake_run):
    result = asyncio.run(_h_get_status(None, {"devices": ["AI核心交换机", "AI接入交换机"]}, {}))
    assert result["targets"] == 2 and result["succeeded"] == 2
    assert len(fake_run) == 2
    assert result["results"][0]["output"] == "<AI核心交换机>回显"


def test_config_keyword_filter(nd_huawei, fake_run):
    from app.agent.netdev_tools import _h_get_config
    asyncio.run(_h_get_config(None, {"devices": ["AI接入交换机"], "keyword": "ospf"}, {}))
    assert fake_run[0][1] == ["display current-configuration | include ospf"]


def test_apply_config_wrapper_and_save(nd_huawei, nd_h3c):
    # 华为：system-view 包裹 + save 需确认
    cmds = _full_commands("huawei", ["description AI-TEST"], "GigabitEthernet0/0/1", True)
    assert cmds == ["system-view", "interface GigabitEthernet0/0/1",
                    "description AI-TEST", "return", "save", "y"]
    # H3C：save force 免交互
    cmds = _full_commands("h3c", ["undo info-center enable"], "", False)
    assert cmds == ["system-view", "undo info-center enable", "return"]
    # 锐捷：configure terminal / end / write
    cmds = _full_commands("ruijie", ["switchport mode trunk"], "", True)
    assert cmds == ["configure terminal", "switchport mode trunk", "end", "write"]


def test_apply_config_plan(nd_h3c, fake_run):
    plan = asyncio.run(_p_apply_config(
        None, {"devices": ["AI核心交换机"], "interface": "GE1/0/1",
               "commands": ["description UPLINK"]}, {}))
    assert plan["title"].startswith("下发网络设备配置")
    assert "AI核心交换机" in plan["detail"] and "system-view" in plan["detail"]
    assert "默认不保存" in plan["warning"]
    plan2 = asyncio.run(_p_apply_config(
        None, {"devices": ["AI核心交换机"], "commands": ["x"], "save": True}, {}))
    assert "并保存" in plan2["warning"]
    # 缺少 commands → 明确报错
    err_plan = asyncio.run(_p_apply_config(None, {"devices": ["AI核心交换机"]}, {}))
    assert "commands 不能为空" in err_plan["error"]


def test_netdev_write_tools_need_confirmation():
    assert TOOLS_BY_NAME["netdev_apply_config"].write is True
    assert TOOLS_BY_NAME["netdev_run_commands"].write is True
    assert TOOLS_BY_NAME["netdev_get_status"].write is False


# ---------- 终端定位 ----------

def test_locate_terminal(monkeypatch, nd_h3c):
    async def fake_collect(group, force=False):
        return {"total": 1, "collected": 1, "failed": 0}

    def fake_search(group, query):
        if query == "192.168.1.100":
            return {"kind": "asset",
                    "hits": [{"device_id": nd_h3c["id"], "device_name": "AI核心交换机",
                              "port": "GE1/0/3", "ip": "192.168.1.100", "mac": "aabbccddeeff",
                              "access": True, "source": "mac-table"}],
                    "arp_refs": [], "primary_hit": "AI核心交换机"}
        return {"kind": "none", "hits": [], "arp_refs": [], "primary_hit": None,
                "reason": "未找到"}

    monkeypatch.setattr(topo, "collect_group", fake_collect)
    monkeypatch.setattr(topo, "search_asset", fake_search)
    hit = asyncio.run(_h_locate_terminal(None, {"query": "192.168.1.100"}, {}))
    assert "AI核心交换机" in hit["_llm_summary"] and "GE1/0/3" in hit["_llm_summary"]
    miss = asyncio.run(_h_locate_terminal(None, {"query": "8.8.8.8"}, {}))
    assert miss["kind"] == "none" and "未定位到" in miss["_llm_summary"]
    none = asyncio.run(_h_locate_terminal(None, {}, {}))
    assert "query" in none["error"]


# ---------- 技能路由与全局上下文 ----------

def test_netdev_skill_routing():
    assert skills.select_skill("查一下ARP表", "netdev").id == "netdev"
    assert skills.select_skill("在交换机上配置接口", "af").id == "netdev"
    # 深信服技能不适用于 netdev 上下文
    assert skills.select_skill("看一下设备运行状态", "netdev") is None
    # 深信服上下文路由不受影响
    assert skills.select_skill("看一下设备运行状态", "af").id == "status"
    assert skills.select_skill("把 445 端口对公网暴露的策略停用", "af").id == "policy-change"
    # netdev 技能目录对所有上下文可见（LLM 兜底路由）
    assert any(s.id == "netdev" for s in skills._skills_for("af"))
    assert all(s.id != "status" for s in skills._skills_for("netdev"))


def test_netdev_skill_unlocks_write_tools():
    skill = skills.select_skill("帮我在交换机上配置接口地址", "af")
    assert skill.id == "netdev"
    names = {t.name for t in skills.resolve_skill_tools(skill, "af")}
    assert "netdev_apply_config" in names and "netdev_run_commands" in names
    # 其他技能不解锁网络设备写工具
    other = skills.select_skill("把 445 端口对公网暴露的策略停用", "af")
    other_names = {t.name for t in skills.resolve_skill_tools(other, "af")}
    assert "netdev_apply_config" not in other_names


def test_device_context_resolution(nd_h3c):
    assert load_any_device(nd_h3c["id"])["id"] == nd_h3c["id"]
    assert device_context_type(nd_h3c) == "netdev"
    assert device_context_type({"id": "dev_x", "type": "af"}) == "af"
    ctx = __import__("app.agent.prompts", fromlist=["device_context_message"]).device_context_message(nd_h3c, None)
    assert "网络设备" in ctx and "H3C" in ctx


# ---------- 编排器 devices 批量 fan-out ----------

@pytest.fixture()
def sf_devices():
    d1 = db.upsert_device({"id": db.new_id("dev_"), "name": "批量-AF-01", "type": "af",
                           "mode": "real", "base_url": "https://10.78.0.1",
                           "username": "a", "password": "b", "readonly": 0,
                           "settings_json": "{}", "created_at": db.now()})
    d2 = db.upsert_device({"id": db.new_id("dev_"), "name": "批量-AC-02", "type": "ac",
                           "mode": "real", "base_url": "http://10.78.0.2:9999",
                           "username": "", "password": "k", "readonly": 0,
                           "settings_json": "{}", "created_at": db.now()})
    yield [d1, d2]
    db.delete_device(d1["id"])
    db.delete_device(d2["id"])


def test_resolve_batch_targets_and_match(sf_devices):
    assert resolve_batch_targets({"devices": ["a", "b"]}) == ["a", "b"]
    assert resolve_batch_targets({"devices": "single"}) == ["single"]
    assert resolve_batch_targets({}) == []
    matched, missing = match_sangfor_devices(["批量-AF-01", "10.78.0.2:9999", "不存在的设备"])
    assert [d["name"] for d in matched] == ["批量-AF-01", "批量-AC-02"]
    assert missing == ["不存在的设备"]


class _FakeTool:
    def __init__(self, name, handler=None, prepare=None, needs_device=True,
                 device_type=None, write=False):
        self.name = name
        self.handler = handler
        self.prepare = prepare
        self.needs_device = needs_device
        self.device_type = device_type
        self.write = write


@pytest.mark.asyncio
async def test_run_read_batch(sf_devices, monkeypatch):
    seen = []

    async def fake_client(device_id):
        return {"device_id": device_id}

    async def handler(client, args, device):
        seen.append((client["device_id"], dict(args)))
        return {"data": device["name"]}

    monkeypatch.setattr("app.agent.orchestrator.get_client", fake_client)
    orch = AgentOrchestrator()
    tool = _FakeTool("fake_read", handler=handler)
    result = await orch._run_read_batch(tool, {"devices": ["批量-AF-01", "批量-AC-02", "缺失设备"]})
    assert result["batch"] is True and result["succeeded"] == 2
    assert result["results"][2]["ok"] is False
    assert len(seen) == 2 and "devices" not in seen[0][1]


@pytest.mark.asyncio
async def test_prepare_write_plan_batch_and_single(sf_devices, monkeypatch):
    guardrails.register_meta("fake_batch_write", {"write": True})
    prepared = []

    async def prepare(client, args, device):
        prepared.append(device["name"])
        return {"title": "修改访问控制策略", "warning": "注意影响",
                "after": {"name": device["name"]}, "op": "update"}

    async def fake_client(device_id):
        return object()

    monkeypatch.setattr("app.agent.orchestrator.get_client", fake_client)
    orch = AgentOrchestrator()
    tool = _FakeTool("fake_batch_write", prepare=prepare)

    # 批量：合并为一张卡片
    plan = await orch._prepare_write_plan(tool, "fake_batch_write",
                                          {"devices": ["批量-AF-01", "批量-AC-02"],
                                           "data": {"enabled": False}},
                                          sf_devices[0], sf_devices[0]["id"])
    assert prepared == ["批量-AF-01", "批量-AC-02"]
    assert "共 2 台设备" in plan["title"] and len(plan["batch_devices"]) == 2

    # 单台：不合并
    plan1 = await orch._prepare_write_plan(tool, "fake_batch_write",
                                           {"data": {"enabled": False}},
                                           sf_devices[0], sf_devices[0]["id"])
    assert "batch_devices" not in plan1 and prepared[-1] == "批量-AF-01"

    # 目标设备只读 → 拦截
    db.upsert_device({**sf_devices[1], "readonly": 1})
    try:
        with pytest.raises(guardrails.GuardrailError):
            await orch._prepare_write_plan(tool, "fake_batch_write",
                                           {"devices": ["批量-AC-02"], "data": {}},
                                           sf_devices[0], sf_devices[0]["id"])
    finally:
        db.upsert_device({**sf_devices[1], "readonly": 0})


@pytest.mark.asyncio
async def test_execute_write_batch(sf_devices, monkeypatch):
    guardrails.register_meta("fake_batch_write", {"write": True})
    executed = []

    async def handler(client, args, device):
        executed.append(device["name"])
        return {"ok": True}

    async def fake_client(device_id):
        return object()

    monkeypatch.setattr("app.agent.orchestrator.get_client", fake_client)
    import app.agent.tools as tools_mod
    tool = _FakeTool("fake_batch_write", handler=handler)
    monkeypatch.setitem(tools_mod.TOOLS_BY_NAME, "fake_batch_write", tool)
    orch = AgentOrchestrator()
    result = await orch._execute_write_batch("fake_batch_write",
                                             {"devices": ["批量-AF-01", "批量-AC-02"], "data": {}},
                                             ["批量-AF-01", "批量-AC-02"], "conv_x")
    assert result["succeeded"] == 2 and executed == ["批量-AF-01", "批量-AC-02"]


def test_list_available_devices_includes_netdev(nd_h3c, sf_devices):
    from app.agent.tools import _h_switch_device
    result = asyncio.run(_h_switch_device(None, {}, sf_devices[0]))
    assert any(n["name"] == "AI核心交换机" for n in result["available_netdev_devices"])
    assert any(d["name"] == "批量-AF-01" for d in result["available_devices"])


# ---------- 全局模式（默认态） ----------

def test_global_context_resolution(sf_devices, nd_h3c):
    ctx = load_any_device("global")
    assert ctx["id"] == "global" and ctx["type"] == "global"
    assert ctx["sangfor_count"] >= 2 and ctx["netdev_count"] >= 1
    assert device_context_type(ctx) == "global"
    from app.agent.prompts import device_context_message
    text = device_context_message(ctx, None)
    assert "全局模式" in text and "devices" in text


def test_global_tool_and_skill_scope(sf_devices):
    names = {t.name for t in get_tools("global")}
    assert "get_nat_rules" in names and "netdev_get_status" in names
    assert "run_config_checkup" in names
    # 全局模式全部技能可见（含深信服系与网络设备技能）
    ids = {s.id for s in skills._skills_for("global")}
    assert {"status", "policy-change", "netdev"} <= ids
    full = get_tools_by_name("global")
    scope = {t.name for t in skills.resolve_skill_tools(None, "global")}
    assert scope == set(full.keys())


def test_match_sangfor_devices_all_token(sf_devices):
    matched, missing = match_sangfor_devices(["all"])
    assert len(matched) >= 2 and missing == []
    matched, missing = match_sangfor_devices(["全部", "批量-AF-01"])
    names = [d["name"] for d in matched]
    assert names.count("批量-AF-01") == 1 and "批量-AC-02" in names and missing == []
    # 无设备时 all 明确回退提示
    import app.agent.orchestrator as orch_mod
    real = orch_mod.db.list_devices
    orch_mod.db.list_devices = lambda: []
    try:
        matched, missing = match_sangfor_devices(["all"])
        assert matched == [] and "尚无深信服设备" in missing[0]
    finally:
        orch_mod.db.list_devices = real


def test_resolve_targets_all_token(nd_h3c, nd_huawei):
    devs, err = _resolve_targets({"devices": ["all"]}, {})
    assert err == "" and {d["id"] for d in devs} == {nd_h3c["id"], nd_huawei["id"]}
    # 无网络设备时 all 给出友好提示
    real = db.list_netdev_devices
    db.list_netdev_devices = lambda: []
    try:
        devs, err = _resolve_targets({"devices": ["all"]}, {})
        assert devs is None and "尚无已添加的网络设备" in err
    finally:
        db.list_netdev_devices = real


def test_global_target_guidance(sf_devices):
    text = _global_target_guidance(db.list_devices())
    assert "全局模式" in text and "批量-AF-01" in text and "all" in text
    empty = _global_target_guidance([])
    assert "尚未添加深信服设备" in empty


@pytest.mark.asyncio
async def test_resume_confirm_global_falls_back_to_conv_device(sf_devices, monkeypatch):
    """全局模式确认变更：执行回退到动作所属会话绑定的设备（不对 global 建连）。"""
    guardrails.register_meta("fake_global_confirm", {"write": True})
    seen = {}

    async def handler(client, args, device):
        seen["device"] = device
        return {"ok": True}

    import app.agent.tools as tools_mod
    monkeypatch.setitem(tools_mod.TOOLS_BY_NAME, "fake_global_confirm",
                        _FakeTool("fake_global_confirm", handler=handler, needs_device=False))
    conv = db.create_conversation("全局回退测试", device_id=sf_devices[0]["id"])
    action = db.create_pending_action({
        "id": db.new_id("act_"), "conv_id": conv["id"], "tool_name": "fake_global_confirm",
        "args_json": '{"tool_call_id": "call_x", "args": {}}', "summary": "测试",
        "status": "pending", "created_at": db.now()})
    orch = AgentOrchestrator()
    events = []
    async for ev in orch.resume_confirm(conv["id"], action["id"], True, "global"):
        events.append(ev)
    assert seen["device"]["id"] == sf_devices[0]["id"]
    assert db.get_pending_action(action["id"])["status"] == "executed"
    assert any(e.get("type") == "confirm_result" and e.get("approved") for e in events)


def test_chat_api_accepts_global_device():
    """设备选择默认全局：device_id=global 可直接对话（离线兜底返回全局模式提示）。"""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "你好", "device_id": "global"})
        assert resp.status_code == 200
        import json as _json
        texts = "".join(_json.loads(line[6:]).get("text", "")
                        for line in resp.text.split("\n") if line.startswith("data: "))
        assert "全局" in texts


# ---------- 演示设备下线后的 API 行为 ----------

def test_devices_api_rejects_simulator():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.post("/api/devices", json={"name": "演示尝试", "type": "af",
                                              "mode": "simulator"})
        assert r.status_code == 400
        assert "已下线" in r.json()["detail"]
        # 默认 mode=real 可正常创建
        r2 = client.post("/api/devices", json={"name": "真实设备-API", "type": "af",
                                               "base_url": "https://10.78.9.9"})
        assert r2.status_code == 200 and r2.json()["mode"] == "real"
        client.delete(f"/api/devices/{r2.json()['id']}")


def test_chat_api_accepts_netdev_device(nd_h3c):
    """网络设备绑定 ID 可直接进入对话（离线兜底模式下返回网络设备提示）。"""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        resp = client.post("/api/chat", json={"message": "看看交换机状态",
                                              "device_id": nd_h3c["id"]})
        assert resp.status_code == 200
        events = [line[6:] for line in resp.text.split("\n") if line.startswith("data: ")]
        texts = "".join(e.get("text", "") for e in map(lambda s: __import__("json").loads(s), events))
        assert "网络设备" in texts
