"""响应优化回归：技能路由写意图门控、记忆提取合并与节流、历史工具结果截断、
知识库超时配置化、升级信息后台刷新。"""
import asyncio

import pytest

from app import db
from app.agent import skills
from app.agent.orchestrator import AgentOrchestrator, _parse_memory_extract
from app.config import settings
from app.services import update_service, zhuge_kb_service


# ---------- 技能路由：写意图门控 ----------

def test_write_intent_detection():
    assert skills.looks_like_write_intent("新建一条 NAT 策略")
    assert skills.looks_like_write_intent("把这条规则删掉")
    assert skills.looks_like_write_intent("放行 443 端口")
    assert not skills.looks_like_write_intent("什么是 HA 主备")
    assert not skills.looks_like_write_intent("设备现在 CPU 多少")
    assert not skills.looks_like_write_intent("")


# ---------- 记忆提取：合并解析与增量节流 ----------

def test_parse_memory_extract_marked():
    text = "[摘要]\n用户查询了设备状态。\n[事实]\n[偏好] 用户喜欢先检查再操作\n设备 AF 8.0.69 正常"
    summary, facts = _parse_memory_extract(text)
    assert summary == "用户查询了设备状态。"
    assert "先检查再操作" in facts


def test_parse_memory_extract_missing_facts_section():
    summary, facts = _parse_memory_extract("[摘要]\n只有摘要内容")
    assert summary == "只有摘要内容" and facts == ""


def test_parse_memory_extract_garbage_degrades_to_empty():
    assert _parse_memory_extract("模型输出完全不符合格式") == ("", "")
    assert _parse_memory_extract("") == ("", "")


@pytest.mark.asyncio
async def test_memory_extraction_throttled_by_new_message_count(device_id):
    """满阈值后每轮不再重复提取：新增消息不足间隔时跳过（不依赖 LLM 即可观察占位标记）。"""
    conv = db.create_conversation("记忆节流测试", device_id=device_id)
    for i in range(6):
        db.add_message(conv["id"], "user", {"text": f"消息{i}"})
    orch = AgentOrchestrator()
    await orch._extract_memory(conv["id"], device_id)   # 首次：进入并记录占位（LLM 未配置即返回）
    assert orch._mem_extracted_at[conv["id"]] == 6
    db.add_message(conv["id"], "user", {"text": "再问一条"})
    await orch._extract_memory(conv["id"], device_id)   # 仅 +1 条：被节流跳过
    assert orch._mem_extracted_at[conv["id"]] == 6
    for i in range(5):
        db.add_message(conv["id"], "user", {"text": f"补量消息{i}"})
    await orch._extract_memory(conv["id"], device_id)   # 新增满 6 条：允许再次提取
    assert orch._mem_extracted_at[conv["id"]] == 12


# ---------- 历史工具结果截断 ----------

def test_build_messages_truncates_old_tool_results(device_id):
    conv = db.create_conversation("工具结果截断测试", device_id=device_id)
    long_text = "X" * 1000
    for i in range(5):
        db.add_message(conv["id"], "tool", {"tool_call_id": f"call_{i}",
                                            "name": "get_status", "content": long_text})
    messages = AgentOrchestrator()._build_messages(conv["id"], {})
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    assert len(tool_msgs) == 5
    assert all(len(m["content"]) == 1000 for m in tool_msgs[-3:])   # 最近 3 条全文
    for m in tool_msgs[:-3]:
        assert len(m["content"]) < 300 and "已省略" in m["content"]
    assert [m["tool_call_id"] for m in tool_msgs] == [f"call_{i}" for i in range(5)]


# ---------- 诸葛知识库：超时参数配置化 ----------

def test_zhuge_timeouts_from_settings():
    assert settings.kb_ask_timeout == 45.0
    assert settings.kb_clarify_grace == 3.0
    assert settings.kb_poll_interval == 0.2
    assert zhuge_kb_service.ASK_TIMEOUT == settings.kb_ask_timeout
    assert zhuge_kb_service._ZhugeSession.CLARIFY_GRACE == settings.kb_clarify_grace


# ---------- 升级信息：stale-while-revalidate ----------

@pytest.mark.asyncio
async def test_stale_cache_refresh_is_backgrounded(device_id, monkeypatch):
    """缓存过期时不再同步等官网抓取：先返回旧缓存，刷新在后台进行。"""
    db.save_update_cache("af", "release_notes", None, "official_platform@test")
    with db._connect() as conn:
        conn.execute("UPDATE update_cache SET fetched_at=? WHERE product='af' "
                     "AND kind='release_notes'",
                     ("2020-01-01T00:00:00",))
    release_gate = asyncio.Event()

    async def blocked_fetch(product):
        await release_gate.wait()   # 模拟官网抓取挂起
        return {"status": "ok", "payload": None}

    monkeypatch.setattr(update_service, "fetch_official_release_notes", blocked_fetch)
    try:
        overview = await asyncio.wait_for(update_service.get_update_overview("AF 8.0.69", "af"),
                                          timeout=2)
        assert overview["current_version"]   # 未被官网抓取阻塞
        assert overview["sources"] and overview["sources"][0].get("stale_refreshing") is True
        task = update_service._refresh_tasks.get("af")
        assert task is not None and not task.done()   # 后台刷新已发起
        release_gate.set()
        await asyncio.wait_for(asyncio.shield(task), timeout=2)
    finally:
        release_gate.set()
        for t in update_service._refresh_tasks.values():
            t.cancel()


@pytest.mark.asyncio
async def test_background_refresh_deduplicated(device_id, monkeypatch):
    """同一产品线的过期刷新在途时去重，不重复抓取。"""
    calls: list[str] = []
    gate = asyncio.Event()

    async def slow_fetch(product):
        calls.append(product)
        await gate.wait()
        return {"status": "error", "reason": "skip"}

    monkeypatch.setattr(update_service, "fetch_official_release_notes", slow_fetch)
    try:
        update_service._schedule_official_refresh("ac")
        update_service._schedule_official_refresh("ac")
        await asyncio.sleep(0.05)
        assert calls == ["ac"]
    finally:
        gate.set()
        for t in update_service._refresh_tasks.values():
            t.cancel()
