"""护栏与 Agent 离线兜底测试。"""
import json

import pytest

from app.agent import guardrails
from app.agent.guardrails import GuardrailError


def test_blacklist_patterns():
    for msg in ["帮我恢复出厂设置", "删除所有管理员账号", "请关闭HA双机", "清空所有策略"]:
        with pytest.raises(GuardrailError):
            guardrails.check_user_request(msg)
    guardrails.check_user_request("把445端口对外的策略停用")   # 正常请求不拦截


def test_write_tool_readonly_device(device_id):
    from app.agent.tools import TOOLS_BY_NAME
    tool = TOOLS_BY_NAME["update_acl_rule"]
    with pytest.raises(GuardrailError):
        guardrails.check_tool_call("update_acl_rule", {"rule_id": "acl-001", "data": {}},
                                   {"name": "t", "readonly": 1})
    # 只读工具不受影响
    guardrails.check_tool_call("get_nat_rules", {}, {"name": "t", "readonly": 1})


def test_guardrail_write_allowed_normally(device_id):
    guardrails.check_tool_call("update_acl_rule", {"rule_id": "acl-001", "data": {"log": True}},
                               {"name": "t", "readonly": 0})


@pytest.mark.asyncio
async def test_offline_chat_flow(device_id):
    """无 LLM Key 的离线兜底：SSE 事件流包含 token 与 done。"""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        resp = c.post("/api/chat", json={"message": "查看NAT策略", "device_id": device_id})
        events = [json.loads(line[6:]) for line in resp.text.split("\n")
                  if line.startswith("data: ")]
    types = [e["type"] for e in events]
    assert types[0] == "meta"
    assert "token" in types and types[-1] == "done"
    text = "".join(e.get("text", "") for e in events if e["type"] == "token")
    assert "NAT 策略" in text


@pytest.mark.asyncio
async def test_blacklist_message_via_api(device_id):
    """黑名单请求直接被拦截并返回 error 事件。"""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        resp = c.post("/api/chat", json={"message": "恢复出厂设置", "device_id": device_id})
        events = [json.loads(line[6:]) for line in resp.text.split("\n")
                  if line.startswith("data: ")]
    assert any(e["type"] == "error" and "禁止" in e.get("text", "") for e in events)
    assert not any(e["type"] == "token" for e in events)


@pytest.mark.asyncio
async def test_write_via_agent_requires_confirmation(device_id):
    """离线模式下写操作不可达；确认流端点对无效 action 返回 404。"""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        resp = c.post("/api/chat/confirm", json={"action_id": "not-exists",
                                                 "device_id": device_id, "approved": True})
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_audit_log_recorded(device_id):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        c.post("/api/chat", json={"message": "看一下设备状态", "device_id": device_id})
        logs = c.get("/api/chat/audit").json()
    assert any(l["action"].startswith("agent.") for l in logs)
