"""确认流端到端：挂起写操作 → 用户确认 → 真正下发设备（含拒绝路径）。"""
import json
import sys

import pytest

from app import db
from app.adapters.factory import get_client

sys.path.insert(0, ".")
from app.agent.tools import TOOLS_BY_NAME  # noqa: E402


async def _prepare_pending(conv_id: str, device_id: str, tool_name: str, args: dict) -> str:
    """模拟编排器生成变更计划并挂起（与 orchestrator 逻辑一致，含内部参数模板合并）。"""
    tool = TOOLS_BY_NAME[tool_name]
    internal = getattr(tool, "internal", None) or {}
    merged = {**internal, **args}
    client = await get_client(device_id)   # 共享客户端，不关闭
    plan = await tool.prepare(client, merged, db.get_device(device_id))
    action = db.create_pending_action({
        "id": db.new_id("act_"), "conv_id": conv_id, "tool_name": tool_name,
        "args_json": json.dumps({"tool_call_id": "call_test", "args": merged},
                                ensure_ascii=False),
        "summary": plan.get("title", tool_name), "status": "pending",
        "created_at": db.now()})
    return action["id"]


@pytest.mark.asyncio
async def test_confirm_flow_approve_executes(device_id):
    from app.agent.orchestrator import AgentOrchestrator
    conv = db.create_conversation("确认流测试")
    action_id = await _prepare_pending(
        conv["id"], device_id, "update_acl_rule",
        {"rule_id": "acl-005", "data": {"log": False}})
    assert db.get_pending_action(action_id)["status"] == "pending"

    orch = AgentOrchestrator()
    async for ev in orch.resume_confirm(conv["id"], action_id, True, device_id):
        if ev["type"] == "confirm_result":
            assert ev["approved"] is True
            break
    assert db.get_pending_action(action_id)["status"] == "executed"

    # 设备侧真实生效
    client = await get_client(device_id)
    rules = {r.id: r for r in await client.get_acl_rules()}
    assert rules["acl-005"].log is False
    # 审计记录
    logs = db.list_audit(limit=30)
    assert any("update_acl_rule" in l["action"] and l["result"] == "executed" for l in logs)


@pytest.mark.asyncio
async def test_confirm_flow_reject(device_id):
    from app.agent.orchestrator import AgentOrchestrator
    conv = db.create_conversation("拒绝流测试")
    action_id = await _prepare_pending(
        conv["id"], device_id, "delete_acl_rule", {"rule_id": "acl-007"})
    client = await get_client(device_id)
    before = {r.id for r in await client.get_acl_rules()}
    assert "acl-007" in before

    orch = AgentOrchestrator()
    async for ev in orch.resume_confirm(conv["id"], action_id, False, device_id):
        if ev["type"] == "confirm_result":
            assert ev["approved"] is False
            break
    assert db.get_pending_action(action_id)["status"] == "rejected"

    client = await get_client(device_id)
    after = {r.id for r in await client.get_acl_rules()}
    assert "acl-007" in after          # 拒绝后规则未被删除


@pytest.mark.asyncio
async def test_double_confirm_rejected(device_id):
    from app.agent.orchestrator import AgentOrchestrator
    conv = db.create_conversation("重复确认测试")
    action_id = await _prepare_pending(
        conv["id"], device_id, "update_acl_rule", {"rule_id": "acl-006", "data": {"log": True}})
    orch = AgentOrchestrator()
    async for ev in orch.resume_confirm(conv["id"], action_id, True, device_id):
        if ev["type"] == "confirm_result":
            break
    got_error = False
    async for ev in orch.resume_confirm(conv["id"], action_id, True, device_id):
        if ev["type"] == "error":
            got_error = True
            break
    assert got_error
