"""体验优化测试：设备工厂可用性保护（per-device 锁/登录超时/负缓存）、会话按设备恢复、消息 LIMIT。"""
import asyncio
import time

import pytest

from app import db
from app.adapters import factory
from app.adapters.base import DeviceError


def _make_device(name, mode="real", base_url="http://127.0.0.1:9"):
    return db.upsert_device({
        "id": db.new_id("dev_"), "name": name, "type": "af", "mode": mode,
        "base_url": base_url, "username": "admin", "password": "x",
        "readonly": 0, "settings_json": "{}", "created_at": db.now(),
    })


# ---------------- 问题1：不可达设备保护 ----------------

@pytest.mark.asyncio
async def test_unreachable_device_fails_fast_then_cooldown():
    """不可达设备：首连快速失败；冷却期内不再发起连接（负缓存）。"""
    dev = _make_device("坏设备", base_url="http://127.0.0.1:9")   # discard 端口，立即拒绝
    t0 = time.monotonic()
    with pytest.raises(DeviceError):
        await factory.get_client(dev["id"])
    assert time.monotonic() - t0 < 5
    t1 = time.monotonic()
    with pytest.raises(DeviceError):
        await factory.get_client(dev["id"])
    assert time.monotonic() - t1 < 0.5   # 未再尝试连接


@pytest.mark.asyncio
async def test_login_failure_negative_cache_and_retry_window(monkeypatch):
    dev = _make_device("故障设备")
    calls = {"n": 0}

    class _FailClient:
        def __init__(self, device):
            self.closed = False

        async def login(self):
            calls["n"] += 1
            raise DeviceError("连接失败")

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(factory, "create_client", lambda device: _FailClient(device))
    with pytest.raises(DeviceError):
        await factory.get_client(dev["id"])
    assert calls["n"] == 1
    # 冷却期内秒速失败，不再触碰设备
    t0 = time.monotonic()
    with pytest.raises(DeviceError):
        await factory.get_client(dev["id"])
    assert time.monotonic() - t0 < 0.2
    assert calls["n"] == 1
    assert factory._clients.get(dev["id"]) is None   # 失败不入缓存
    # 冷却结束（人工拨回）后允许重试
    factory._failed_until[dev["id"]] = time.monotonic() - 0.1
    with pytest.raises(DeviceError):
        await factory.get_client(dev["id"])
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_login_timeout_raises_device_error(monkeypatch):
    dev = _make_device("挂起设备")

    class _HangingClient:
        def __init__(self, device):
            self.closed = False

        async def login(self):
            await asyncio.sleep(5)

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(factory, "create_client", lambda device: _HangingClient(device))
    monkeypatch.setattr(factory.settings, "device_login_timeout", 0.2)
    t0 = time.monotonic()
    with pytest.raises(DeviceError, match="登录超时"):
        await factory.get_client(dev["id"])
    assert time.monotonic() - t0 < 2


@pytest.mark.asyncio
async def test_per_device_lock_other_device_unblocked(monkeypatch):
    """一台设备登录挂起不阻塞另一设备的登录（per-device 锁）。"""
    dev_bad = _make_device("慢设备")
    dev_ok = _make_device("好模拟器", mode="simulator", base_url="")
    gate = asyncio.Event()
    lock_held = asyncio.Event()

    class _FakeClient:
        def __init__(self, device):
            self.device = device
            self.closed = False

        async def login(self):
            if self.device["id"] == dev_bad["id"]:
                lock_held.set()
                await gate.wait()   # 模拟慢登录挂起
            return True

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(factory, "create_client", lambda device: _FakeClient(device))

    bad_task = asyncio.ensure_future(factory.get_client(dev_bad["id"]))
    await lock_held.wait()   # 坏设备已持锁挂起
    t0 = time.monotonic()
    ok_client = await asyncio.wait_for(factory.get_client(dev_ok["id"]), timeout=1)
    assert time.monotonic() - t0 < 1   # 未被坏设备的锁阻塞
    assert ok_client is factory._clients.get(dev_ok["id"])
    # 坏设备在 1s 内完不成（仍挂起），随后放行并清理
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(bad_task), timeout=1)
    gate.set()
    await asyncio.wait_for(bad_task, timeout=1)


# ---------------- 问题2：会话按设备恢复 ----------------

def test_conversation_device_id_column_and_latest():
    with db._connect() as conn:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)")]
    assert "device_id" in cols

    dev = "dev_" + db.new_id()
    c1 = db.create_conversation("会话A", device_id=dev)
    c2 = db.create_conversation("会话B", device_id=dev)
    with db._connect() as conn:
        conn.execute("UPDATE conversations SET updated_at='2099-01-01T00:00:00' WHERE id=?", (c2["id"],))
    latest = db.latest_conversation_by_device(dev)
    assert latest["id"] == c2["id"]
    assert db.latest_conversation_by_device("dev_不存在") is None


def test_last_conversation_endpoint_shape():
    from app.api.chat import last_conversation
    dev = _make_device("恢复设备", mode="simulator", base_url="")
    assert last_conversation(dev["id"])["conv_id"] is None

    conv = db.create_conversation("历史恢复", device_id=dev["id"])
    db.add_message(conv["id"], "user", {"text": "你好"})
    db.add_message(conv["id"], "assistant", {
        "text": "您好", "tool_calls": [{"id": "t1", "name": "get_device_status", "arguments": "{}"}]})
    db.add_message(conv["id"], "tool", {"tool_call_id": "t1", "name": "get_device_status", "content": "{}"})
    r = last_conversation(dev["id"])
    assert r["conv_id"] == conv["id"]
    assert len(r["messages"]) == 3
    assert r["pending_action"] is None

    db.create_pending_action({
        "id": db.new_id("act_"), "conv_id": conv["id"], "tool_name": "update_acl_rule",
        "args_json": "{}", "summary": "停用暴露策略", "status": "pending", "created_at": db.now()})
    r2 = last_conversation(dev["id"])
    assert r2["pending_action"]["summary"] == "停用暴露策略"
    assert r2["pending_action"]["action_id"].startswith("act_")


# ---------------- 问题3：消息 LIMIT ----------------

def test_get_messages_limit_keeps_latest_in_order():
    conv = db.create_conversation("limit测试")
    for i in range(30):
        db.add_message(conv["id"], "user", {"text": f"m{i}"})
    assert len(db.get_messages(conv["id"])) == 30
    last5 = db.get_messages(conv["id"], limit=5)
    assert [m["content"]["text"] for m in last5] == [f"m{i}" for i in range(25, 30)]
