"""第三批第 2 步回归：设备可视化端点 TTL 缓存（A-5）、数据保留清理（N-2）。

缓存边界：只缓存在 API 层（写路径 tool.prepare 直调适配器不受影响）、写后失效、
single-flight 合并同 key 并发加载。
"""
import asyncio

import pytest

from app import db
from app.services import config_service
from app.services.device_cache import DeviceCache, device_cache


# ---------- 设备缓存：命中 / 失效 / single-flight ----------

@pytest.mark.asyncio
async def test_device_cache_hit_and_invalidate():
    calls = {"n": 0}

    async def loader() -> dict:
        calls["n"] += 1
        return {"v": calls["n"]}

    cache = DeviceCache()
    assert await cache.get_or_load("dev1", "status", 30, loader) == {"v": 1}
    assert await cache.get_or_load("dev1", "status", 30, loader) == {"v": 1}
    assert await cache.get_or_load("dev2", "status", 30, loader) == {"v": 2}
    assert calls["n"] == 2, "同设备同 key 命中缓存，不同设备独立"
    cache.invalidate("dev1")
    assert await cache.get_or_load("dev1", "status", 30, loader) == {"v": 3}
    assert calls["n"] == 3, "失效后重新加载"
    assert await cache.get_or_load("dev2", "status", 30, loader) == {"v": 2}, "失效只影响目标设备"


@pytest.mark.asyncio
async def test_device_cache_single_flight():
    calls = {"n": 0}
    gate = asyncio.Event()

    async def slow_loader() -> str:
        calls["n"] += 1
        await gate.wait()
        return "loaded"

    cache = DeviceCache()
    gather_task = asyncio.gather(*(cache.get_or_load("d", "k", 30, slow_loader) for _ in range(5)))
    await asyncio.sleep(0.05)   # 等 4 个并发请求进入 single-flight 等待队列
    gate.set()
    results = await gather_task
    assert all(r == "loaded" for r in results)
    assert calls["n"] == 1, "并发同 key 请求合并为一次加载（single-flight）"


@pytest.mark.asyncio
async def test_endpoints_cache_hit_and_invalidation(device_id, monkeypatch):
    """/status 端点走缓存；确认写执行后该设备缓存失效。"""
    from app.agent.orchestrator import AgentOrchestrator
    from app.adapters.factory import get_client

    calls = {"n": 0}
    client = await get_client(device_id)
    orig = type(client).get_status

    async def counting_get_status(self):
        calls["n"] += 1
        return await orig(self)

    monkeypatch.setattr(type(client), "get_status", counting_get_status)

    from app.api.devices import get_status as ep_status
    await ep_status(device_id)
    await ep_status(device_id)
    assert calls["n"] == 1, "TTL 内第二次请求命中缓存，不重复实拉设备"

    device_cache.invalidate(device_id)
    await ep_status(device_id)
    assert calls["n"] == 2, "失效后重新拉取"

    # 写执行路径（resume_confirm 批准）后缓存失效：确认一次写操作，下一次读取应重新拉取
    import json as _json
    from app.agent.tools import TOOLS_BY_NAME
    tool = TOOLS_BY_NAME["update_acl_rule"]
    merged = {**(getattr(tool, "internal", None) or {}), "rule_id": "acl-006", "data": {"log": True}}
    plan = await tool.prepare(client, merged, db.get_device(device_id))
    action = db.create_pending_action({
        "id": db.new_id("act_"), "conv_id": (conv := db.create_conversation("缓存失效测试", device_id=device_id))["id"],
        "tool_name": "update_acl_rule",
        "args_json": _json.dumps({"tool_call_id": "call_cache", "args": merged}, ensure_ascii=False),
        "summary": plan.get("title", ""), "status": "pending", "created_at": db.now()})
    orch = AgentOrchestrator()
    async for _ev in orch.resume_confirm(conv["id"], action["id"], True, device_id):
        break
    n_before = calls["n"]   # 写路径 prepare/handler 直调设备（绕过缓存，符合边界设计），计数含这些调用
    await ep_status(device_id)
    assert calls["n"] == n_before + 1, "写执行后设备缓存已失效，下一次读取重新拉取"


# ---------- 数据保留清理（N-2） ----------

def test_cleanup_expired_convs_and_audit(device_id):
    from datetime import datetime, timedelta
    old_ts = (datetime.now() - timedelta(days=200)).isoformat(timespec="seconds")
    fresh_ts = db.now()

    old_conv = db.create_conversation("过期会话", device_id=device_id)
    db.add_message(old_conv["id"], "user", {"text": "旧消息"})
    db.add_message(old_conv["id"], "assistant", {"text": "旧回复"})
    fresh_conv = db.create_conversation("新会话", device_id=device_id)
    db.add_message(fresh_conv["id"], "user", {"text": "新消息"})
    # add_message 会 touch 会话的 updated_at，旧时间戳必须在消息写入之后设置
    with db._connect() as conn:
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (old_ts, old_conv["id"]))

    removed = db.cleanup_expired(retention_days=180, audit_days=0)
    assert removed.get("conversations") == 1
    assert removed.get("messages") == 2
    assert db.get_conversation(old_conv["id"]) is None
    assert db.get_conversation(fresh_conv["id"]) is not None
    assert len(db.get_messages(fresh_conv["id"])) == 1, "新会话及其消息不受影响"

    # 审计日志保留：插入一条超过 365 天保留期的记录（200 天前未过期，不能复用 old_ts）
    ancient_ts = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    with db._connect() as conn:
        conn.execute("INSERT INTO audit_logs (ts, conv_id, device_id, actor, action, detail_json, result)"
                     " VALUES (?, '', '', 'test', 'cleanup.test', '{}', 'ok')", (ancient_ts,))
        audit_id = conn.execute("SELECT MAX(id) AS m FROM audit_logs").fetchone()["m"]
    db.cleanup_expired(retention_days=0, audit_days=365)
    with db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS c FROM audit_logs WHERE id=?", (audit_id,)).fetchone()["c"] == 0


def test_cleanup_keeps_pending_actions_and_bindings(device_id):
    """pending 状态待确认动作与渠道绑定不因会话过期而丢失（绑定仅解绑）。"""
    from datetime import datetime, timedelta
    old_ts = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    conv = db.create_conversation("绑定会话", device_id=device_id)
    with db._connect() as conn:
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (old_ts, conv["id"]))
    db.create_pending_action({
        "id": db.new_id("act_"), "conv_id": conv["id"], "tool_name": "update_acl_rule",
        "args_json": "{}", "summary": "待确认", "status": "pending", "created_at": db.now()})
    db.upsert_channel_binding("wecom", "user-clean", conv_id=conv["id"], device_id=device_id)

    db.cleanup_expired(retention_days=180, audit_days=0)

    assert db.get_pending_action_by_conv(conv["id"]) is not None, "pending 动作保留"
    binding = db.get_channel_binding("wecom", "user-clean")
    assert binding is not None and binding["conv_id"] == "", "渠道绑定保留但解绑过期会话"


def test_cleanup_scheduled_backups_keeps_recent_and_manual(device_id, tmp_path):
    """每设备 scheduled 备份保留最近 N 份；manual 备份永不清理；配置文件同步删除。"""
    keep_files = []
    for i in range(5):
        p = tmp_path / f"b{i}.conf"
        p.write_text(f"conf-{i}", encoding="utf-8")
        keep_files.append(str(p))
        db.create_backup({"id": db.new_id("bk_"), "device_id": device_id, "label": f"定时{i}",
                          "kind": "scheduled", "sw_version": "", "snapshot_json": "{}",
                          "file_path": str(p), "file_sha256": "", "created_at": f"2026-09-{i + 1:02d}T00:00:00",
                          "created_by": "scheduler"})
    manual = db.create_backup({"id": db.new_id("bk_"), "device_id": device_id, "label": "手动",
                               "kind": "manual", "sw_version": "", "snapshot_json": "{}",
                               "file_path": "", "file_sha256": "", "created_at": "2026-09-01T00:00:00",
                               "created_by": "user"})

    removed = config_service.cleanup_scheduled_backups(keep=2)
    assert removed == 3
    rows = db.list_backups(device_id)
    scheduled = [b for b in rows if b["kind"] == "scheduled"]
    assert len(scheduled) == 2 and all(b["created_at"] >= "2026-09-04" for b in scheduled)
    assert any(b["id"] == manual["id"] for b in rows), "manual 备份不被清理"
    assert not [keep_files[i] for i in range(3) if __import__("os").path.exists(keep_files[i])], \
        "被清理备份的配置文件已删除"
