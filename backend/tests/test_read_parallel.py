"""只读工具整轮并行化（E-1）回归。

快路径关键约束：
- 一轮内全部为可执行只读调用时并发执行（并发重叠可观察），延迟不再线性叠加；
- 写操作 / 未知工具 / 全局模式缺 devices 的轮次回退串行路径（确认流零改动）；
- 结果按原调用顺序落库，tool_call_id 与 tool 消息一一对应；
- 同参数重复调用不重复执行（合并提示）；
- 失败调用不写入 executed_results，同参数重复调用复用失败原因而非重试。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app import db
from app.agent.orchestrator import AgentOrchestrator
from app.agent.tools import TOOLS_BY_NAME


# ---------- 假 LLM：按脚本逐轮回放流式 chunk ----------

class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)


class _FakeCompletions:
    def __init__(self, rounds):
        self._rounds = [list(r) for r in rounds]

    async def create(self, **kwargs):
        return _AsyncIter(self._rounds.pop(0) if self._rounds else [])


class _FakeLLM:
    def __init__(self, rounds):
        self.chat = SimpleNamespace(completions=_FakeCompletions(rounds))


def _text_chunk(text):
    delta = SimpleNamespace(content=text, tool_calls=[])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_call_chunk(idx, call_id, name, arguments):
    tc = SimpleNamespace(index=idx, id=call_id,
                         function=SimpleNamespace(name=name, arguments=arguments))
    delta = SimpleNamespace(content=None, tool_calls=[tc])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _setup_orch(monkeypatch, rounds):
    orch = AgentOrchestrator()
    fake = _FakeLLM(rounds)   # 单实例：脚本跨轮消费（orch._llm() 每轮调用一次）
    monkeypatch.setattr(orch, "_llm", lambda: fake)
    monkeypatch.setattr(orch, "_schedule_memory_extraction", lambda *a, **k: None)
    return orch


async def _collect(orch, conv_id, device_id):
    device = db.get_device(device_id)
    return [ev async for ev in orch._run_llm_loop(conv_id, device_id, device,
                                                  user_message="看看设备情况")]


# ---------- 并发执行与顺序落库 ----------

@pytest.mark.asyncio
async def test_read_round_runs_concurrently_and_ordered(device_id, monkeypatch):
    overlap = {"cur": 0, "peak": 0}

    def wrap(name):
        tool = TOOLS_BY_NAME[name]
        orig = tool.handler

        async def handler(client, args, device):
            overlap["cur"] += 1
            overlap["peak"] = max(overlap["peak"], overlap["cur"])
            await asyncio.sleep(0.05)
            overlap["cur"] -= 1
            return await orig(client, args, device)

        monkeypatch.setattr(tool, "handler", handler)

    for name in ("get_device_status", "get_zones", "get_interfaces"):
        wrap(name)

    orch = _setup_orch(monkeypatch, [
        [_tool_call_chunk(0, "call_1", "get_device_status", "{}"),
         _tool_call_chunk(1, "call_2", "get_zones", "{}"),
         _tool_call_chunk(2, "call_3", "get_interfaces", "{}")],
        [_text_chunk("完成")],
    ])
    conv = db.create_conversation("并行读测试", device_id=device_id)
    events = await _collect(orch, conv["id"], device_id)

    assert overlap["peak"] >= 2, "只读调用应存在并发重叠（并行执行）"
    assert not [e for e in events if e["type"] == "error"]
    # SSE 事件：tool_call 与 tool_result 均按原调用顺序产出
    assert [e["name"] for e in events if e["type"] == "tool_call"] == \
        ["get_device_status", "get_zones", "get_interfaces"]
    assert [e["name"] for e in events if e["type"] == "tool_result"] == \
        ["get_device_status", "get_zones", "get_interfaces"]
    # 落库：assistant 的 tool_calls 与 tool 消息一一对应、顺序一致
    msgs = db.get_messages(conv["id"])
    assistant = next(m for m in msgs if m["role"] == "assistant" and m["content"].get("tool_calls"))
    tool_msgs = [m for m in msgs if m["role"] == "tool"]
    assert [t["id"] for t in assistant["content"]["tool_calls"]] == \
        [m["content"]["tool_call_id"] for m in tool_msgs]
    assert [m["content"]["name"] for m in tool_msgs] == \
        ["get_device_status", "get_zones", "get_interfaces"]


# ---------- 同轮重复调用去重 ----------

@pytest.mark.asyncio
async def test_read_round_dedups_identical_calls(device_id, monkeypatch):
    calls = {"n": 0}
    tool = TOOLS_BY_NAME["get_device_status"]
    orig = tool.handler

    async def handler(client, args, device):
        calls["n"] += 1
        return await orig(client, args, device)

    monkeypatch.setattr(tool, "handler", handler)

    orch = _setup_orch(monkeypatch, [
        [_tool_call_chunk(0, "call_1", "get_device_status", "{}"),
         _tool_call_chunk(1, "call_2", "get_device_status", "{}")],
        [_text_chunk("完成")],
    ])
    conv = db.create_conversation("去重测试", device_id=device_id)
    events = await _collect(orch, conv["id"], device_id)

    assert calls["n"] == 1, "同参数重复调用不应重复执行"
    previews = [e["preview"] for e in events if e["type"] == "tool_result"]
    assert any("重复调用已合并" in p for p in previews)


# ---------- 含写操作的轮次回退串行路径并挂起确认 ----------

@pytest.mark.asyncio
async def test_write_round_stays_sequential_and_suspends(device_id, monkeypatch):
    order = []
    tool = TOOLS_BY_NAME["get_device_status"]
    orig = tool.handler

    async def handler(client, args, device):
        order.append("read")
        return await orig(client, args, device)

    monkeypatch.setattr(tool, "handler", handler)

    orch = _setup_orch(monkeypatch, [
        [_tool_call_chunk(0, "call_1", "get_device_status", "{}"),
         _tool_call_chunk(1, "call_2", "update_acl_rule",
                          json.dumps({"rule_id": "acl-005", "data": {"log": False}}))],
    ])
    conv = db.create_conversation("写挂起测试", device_id=device_id)
    events = await _collect(orch, conv["id"], device_id)

    assert order == ["read"], "写操作前的只读工具应先执行"
    confirms = [e for e in events if e["type"] == "confirm_required"]
    assert len(confirms) == 1 and confirms[0]["action"]["tool_name"] == "update_acl_rule"
    assert not [e for e in events if e["type"] == "tool_result"
                and "error" in str(e.get("preview", "")).lower()]
    action = db.get_pending_action(confirms[0]["action"]["action_id"])
    assert action["status"] == "pending"


# ---------- 失败调用的重复请求复用失败原因 ----------

@pytest.mark.asyncio
async def test_read_failure_dedup_reuses_error(device_id, monkeypatch):
    calls = {"n": 0}
    tool = TOOLS_BY_NAME["get_device_status"]
    orig = tool.handler

    async def failing(client, args, device):
        calls["n"] += 1
        raise RuntimeError("设备超时")

    monkeypatch.setattr(tool, "handler", failing)

    orch = _setup_orch(monkeypatch, [
        [_tool_call_chunk(0, "call_1", "get_device_status", "{}"),
         _tool_call_chunk(1, "call_2", "get_device_status", "{}")],
        [_text_chunk("已了解失败原因")],
    ])
    conv = db.create_conversation("失败去重测试", device_id=device_id)
    events = await _collect(orch, conv["id"], device_id)

    assert calls["n"] == 1, "失败调用的同参数重复请求不应重试执行"
    errs = [e["preview"] for e in events if e["type"] == "tool_result"]
    assert errs.count("工具执行失败：设备超时") == 2, "重复调用复用同一失败原因"
    assert any(e["type"] == "done" for e in events)
