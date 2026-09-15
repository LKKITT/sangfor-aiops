"""外部渠道网关：绑定复用、token 聚合、确认流、白名单、只读模式、管理指令。"""
import pytest

from app import db
from app.config import settings
from app.services import channel_gateway


def _fake_stream(events: list[dict], captured: dict):
    """构造假的编排器 stream_chat：按脚本吐事件并记录调用参数。"""

    async def stream_chat(conv_id, user_message, device_id, use_knowledge=False):
        captured["conv_id"] = conv_id
        captured["message"] = user_message
        captured["device_id"] = device_id
        for ev in events:
            yield ev

    return stream_chat


@pytest.fixture()
def fake_stream(monkeypatch):
    """替换网关使用的编排器 stream_chat，返回 (设置脚本, 调用参数记录)。"""
    captured: dict = {}
    holder: dict = {"events": []}

    def _set(events: list[dict]):
        holder["events"] = events
        monkeypatch.setattr(channel_gateway.orchestrator, "stream_chat",
                            _fake_stream(events, captured))

    return _set, captured


@pytest.fixture()
def fake_resume(monkeypatch):
    """替换 resume_confirm，记录调用参数并回 confirm_result 事件。"""
    seen: dict = {}

    async def resume(conv_id, action_id, approved, device_id, edited=None):
        seen.update({"conv_id": conv_id, "action_id": action_id,
                     "approved": approved, "device_id": device_id})
        yield {"type": "confirm_result", "action_id": action_id, "approved": approved}

    monkeypatch.setattr(channel_gateway.orchestrator, "resume_confirm", resume)
    return seen


def _pending_action(conv_id: str) -> dict:
    return db.create_pending_action({
        "id": db.new_id("act_"), "conv_id": conv_id, "tool_name": "update_acl_rule",
        "args_json": "{}", "summary": "测试变更", "status": "pending",
        "created_at": db.now()})


@pytest.mark.asyncio
async def test_token_aggregation_and_binding_reuse(device_id, fake_stream):
    set_events, captured = fake_stream
    set_events([{"type": "meta", "conv_id": ""}, {"type": "token", "text": "当前"},
                {"type": "token", "text": "CPU 使用率 12%"}, {"type": "done"}])
    db.upsert_channel_binding("wecom", "u1", device_id=device_id)
    r1 = await channel_gateway.run_channel_message("wecom", "u1", "看下设备状态")
    r2 = await channel_gateway.run_channel_message("wecom", "u1", "再来一条")
    assert r1["ok"] and "".join(r1["replies"]) == "当前CPU 使用率 12%"
    assert r1["device_id"] == device_id
    # 第二条消息续接同一会话，绑定记录落库
    assert captured["conv_id"] == r1["conv_id"]
    binding = db.get_channel_binding("wecom", "u1")
    assert binding["conv_id"] == r1["conv_id"] and binding["device_id"] == device_id


@pytest.mark.asyncio
async def test_confirm_required_readonly_hint(device_id, fake_stream, monkeypatch):
    monkeypatch.setattr(settings, "channel_readonly", True)
    set_events, _ = fake_stream
    action = {"action_id": "act_abc123", "tool_name": "update_acl_rule", "title": "修改规则"}
    set_events([{"type": "token", "text": "已生成变更计划"},
                {"type": "confirm_required", "action": action}])
    r = await channel_gateway.run_channel_message("wecom", "u2", "关闭规则日志")
    text = "\n".join(r["replies"])
    assert "待确认变更" in text and "只读模式" in text and "act_abc123" in text
    assert r["pending"] == {"action_id": "act_abc123", "tool_name": "update_acl_rule",
                            "summary": "修改规则"}


@pytest.mark.asyncio
async def test_confirm_command_execute_and_reject(device_id, fake_stream, fake_resume,
                                                  monkeypatch):
    monkeypatch.setattr(settings, "channel_readonly", False)
    conv = db.create_conversation("渠道确认测试", device_id=device_id)
    db.upsert_channel_binding("wecom", "u3", conv_id=conv["id"], device_id=device_id)
    action = _pending_action(conv["id"])

    r = await channel_gateway.run_channel_message("wecom", "u3", f"确认 {action['id']}")
    assert fake_resume == {"conv_id": conv["id"], "action_id": action["id"],
                           "approved": True, "device_id": device_id}
    assert "已确认执行" in "\n".join(r["replies"])

    r2 = await channel_gateway.run_channel_message("wecom", "u3", f"取消 {action['id']}")
    assert fake_resume["approved"] is False
    assert "已拒绝" in "\n".join(r2["replies"])


@pytest.mark.asyncio
async def test_confirm_denied_cross_user(device_id, fake_stream, fake_resume, monkeypatch):
    monkeypatch.setattr(settings, "channel_readonly", False)
    conv = db.create_conversation("越权确认测试", device_id=device_id)
    db.upsert_channel_binding("wecom", "owner", conv_id=conv["id"], device_id=device_id)
    action = _pending_action(conv["id"])

    # 他人发送确认指令：动作不属于其绑定会话，拒绝且不触碰编排器
    r1 = await channel_gateway.run_channel_message("wecom", "intruder", f"确认 {action['id']}")
    assert not r1["ok"] and "不属于当前会话" in "\n".join(r1["replies"])
    assert not fake_resume
    assert db.get_pending_action(action["id"])["status"] == "pending"


@pytest.mark.asyncio
async def test_confirm_blocked_in_readonly(device_id, fake_stream, fake_resume, monkeypatch):
    monkeypatch.setattr(settings, "channel_readonly", True)
    conv = db.create_conversation("只读确认测试", device_id=device_id)
    db.upsert_channel_binding("wecom", "owner2", conv_id=conv["id"], device_id=device_id)
    action = _pending_action(conv["id"])

    r = await channel_gateway.run_channel_message("wecom", "owner2", f"确认 {action['id']}")
    assert "只读模式" in "\n".join(r["replies"])
    assert not fake_resume
    assert db.get_pending_action(action["id"])["status"] == "pending"


@pytest.mark.asyncio
async def test_sender_allowlist(device_id, monkeypatch):
    monkeypatch.setattr(settings, "wecom_allowed_users", "boss, ops")
    r = await channel_gateway.run_channel_message("wecom", "stranger", "你好")
    assert not r["ok"] and "权限" in r["replies"][0]
    r2 = await channel_gateway.run_channel_message("wecom", "boss", "帮助")
    assert r2["ok"] and "管理指令" in r2["replies"][0]


@pytest.mark.asyncio
async def test_manage_commands(device_id, fake_stream):
    set_events, _captured = fake_stream
    set_events([{"type": "token", "text": "ok"}, {"type": "done"}])
    conv = db.create_conversation("管理指令测试", device_id=device_id)
    db.upsert_channel_binding("wecom", "u4", conv_id=conv["id"], device_id=device_id)

    r1 = await channel_gateway.run_channel_message("wecom", "u4", "设备列表")
    assert "设备列表" in r1["replies"][0] and "▶" in r1["replies"][0]
    # 切换设备同时重置会话：旧会话日志保持原归属，后续消息在新设备下开启新会话
    r2 = await channel_gateway.run_channel_message("wecom", "u4", f"切换设备 {device_id}")
    assert "已切换" in r2["replies"][0] and "新会话" in r2["replies"][0]
    b = db.get_channel_binding("wecom", "u4")
    assert b["device_id"] == device_id and b["conv_id"] == ""
    r2b = await channel_gateway.run_channel_message("wecom", "u4", "看下状态")
    assert r2b["conv_id"] != conv["id"]
    assert db.get_conversation(r2b["conv_id"])["device_id"] == device_id
    r3 = await channel_gateway.run_channel_message("wecom", "u4", "切换设备 不存在的设备")
    assert "未找到匹配" in r3["replies"][0]
    r4 = await channel_gateway.run_channel_message("wecom", "u4", "新会话")
    assert "新会话" in r4["replies"][0]
    b = db.get_channel_binding("wecom", "u4")
    assert b["conv_id"] == "" and b["device_id"] == device_id


def test_default_device_prefers_simulator():
    real1, real2, sim = ({"id": "d1", "mode": "real"}, {"id": "d2", "mode": "real"},
                         {"id": "d3", "mode": "simulator"})
    assert channel_gateway._default_device([real1, sim]) is sim
    assert channel_gateway._default_device([sim, real1]) is sim
    assert channel_gateway._default_device([real1, real2]) is real1


@pytest.mark.asyncio
async def test_fresh_sender_binds_demo_device(device_id, fake_stream):
    """新发送方初始绑定默认指向演示设备（模拟器优先）。"""
    set_events, _captured = fake_stream
    set_events([{"type": "token", "text": "ok"}, {"type": "done"}])
    r = await channel_gateway.run_channel_message("wecom", "fresh_u", "你好")
    assert db.get_device(r["device_id"])["mode"] == "simulator"


@pytest.mark.asyncio
async def test_session_timeout_opens_new_conversation(device_id, fake_stream, monkeypatch):
    """长时间未对话：下次消息自动开启新会话并提示。"""
    from datetime import datetime, timedelta
    monkeypatch.setattr(settings, "channel_session_timeout_min", 30)
    set_events, captured = fake_stream
    set_events([{"type": "token", "text": "ok"}, {"type": "done"}])
    db.upsert_channel_binding("wecom", "stale_u", device_id=device_id)
    old_conv = db.create_conversation("旧会话", device_id=device_id)
    stale_time = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    with db._connect() as conn:
        conn.execute("UPDATE channel_bindings SET conv_id=?, last_active_at=?"
                     " WHERE channel='wecom' AND sender_id='stale_u'",
                     (old_conv["id"], stale_time))

    r = await channel_gateway.run_channel_message("wecom", "stale_u", "继续问")
    assert r["conv_id"] != old_conv["id"] and captured["conv_id"] == r["conv_id"]
    assert "自动开启新会话" in r["replies"][0]
    assert db.get_channel_binding("wecom", "stale_u")["conv_id"] == r["conv_id"]
    # 刚活跃过则续接同一会话，不再提示
    r2 = await channel_gateway.run_channel_message("wecom", "stale_u", "再问一条")
    assert r2["conv_id"] == r["conv_id"] and "自动开启" not in r2["replies"][0]


@pytest.mark.asyncio
async def test_session_timeout_disabled(device_id, fake_stream, monkeypatch):
    """超时设为 0：关闭自动新开会话，旧会话一直续接。"""
    from datetime import datetime, timedelta
    monkeypatch.setattr(settings, "channel_session_timeout_min", 0)
    set_events, _captured = fake_stream
    set_events([{"type": "token", "text": "ok"}, {"type": "done"}])
    db.upsert_channel_binding("wecom", "stale_u2", device_id=device_id)
    old_conv = db.create_conversation("旧会话", device_id=device_id)
    stale_time = (datetime.now() - timedelta(hours=8)).isoformat(timespec="seconds")
    with db._connect() as conn:
        conn.execute("UPDATE channel_bindings SET conv_id=?, last_active_at=?"
                     " WHERE channel='wecom' AND sender_id='stale_u2'",
                     (old_conv["id"], stale_time))

    r = await channel_gateway.run_channel_message("wecom", "stale_u2", "继续问")
    assert r["conv_id"] == old_conv["id"]
    assert "自动开启" not in r["replies"][0]


def test_split_reply_long_text():
    text = "\n".join(f"第{i}行内容" * 30 for i in range(200))
    segments = channel_gateway._split_reply(text)
    assert 2 <= len(segments) <= channel_gateway.MAX_SEGMENTS
    assert all(len(s) <= channel_gateway.SEGMENT_LIMIT + 100 for s in segments)
    assert channel_gateway._split_reply("短文本") == ["短文本"]


def test_parse_confirm_command():
    assert channel_gateway.parse_confirm_command("确认 act_ab12cd34") == (True, "act_ab12cd34")
    assert channel_gateway.parse_confirm_command("取消：act_ab12cd34") == (False, "act_ab12cd34")
    assert channel_gateway.parse_confirm_command("Approve act_ab12cd34") == (True, "act_ab12cd34")
    assert channel_gateway.parse_confirm_command("帮我确认一下设备状态") is None
    assert channel_gateway.parse_confirm_command("确认") is None


@pytest.mark.asyncio
async def test_wecom_settings_ui_override_and_hot_apply(device_id, monkeypatch):
    """平台设置保存企微配置：界面值(DB) 覆盖 .env，并触发长连接热重启。"""
    import httpx
    from app.main import app
    from app.services import app_settings, wecom_bot_service

    applied: list = []

    async def fake_apply():
        applied.append(True)

    monkeypatch.setattr(wecom_bot_service, "apply_config", fake_apply)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.post("/api/settings", json={
            "wecom_aibot_enabled": True, "wecom_aibot_id": "bot-123",
            "wecom_aibot_secret": "sec-abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["wecom_enabled"] is True and body["wecom_bot_id"] == "bot-123"
    assert body["wecom_secret_set"] is True and body["wecom_source"] == "database"
    assert body["wecom_conn_status"] in ("disabled", "connecting", "connected")
    assert applied == [True]   # 长连接按新配置热重启
    cfg = app_settings.get_wecom_config()
    assert cfg["enabled"] and cfg["bot_id"] == "bot-123" and cfg["secret"] == "sec-abc"

    # 关闭开关：DB 的 false 覆盖 .env，同样触发热更新
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        r2 = await ac.post("/api/settings", json={"wecom_aibot_enabled": False})
    assert r2.json()["wecom_enabled"] is False and len(applied) == 2

    # 清理 DB 配置，恢复 .env 口径，避免影响其他用例
    db.set_setting(app_settings.WECOM_ENABLED_KEY, "")
    db.set_setting(app_settings.WECOM_ID_KEY, "")
    db.set_setting(app_settings.WECOM_SECRET_KEY, "")


def test_parse_switch_keyword_variants():
    p = channel_gateway._parse_switch_keyword
    assert p("切换设备 演示") == "演示"
    assert p("切换到演示") == "演示"
    assert p("切到AC") == "AC"
    assert p("把设备切换到AF模拟器") == "AF模拟器"
    assert p("帮我切换设备到10.1.1.1") == "10.1.1.1"
    assert p("切换一下设备") is None            # 无有效目标
    assert p("怎么切换设备？") is None          # 疑问句不拦截
    assert p("查看主备切换记录") is None        # 普通提问不误判
    assert p("正常聊天内容") is None


@pytest.mark.asyncio
async def test_natural_language_session_and_switch(device_id, fake_stream):
    """自然语言的新会话/切换设备意图短路处理：不进编排器、不触碰设备连接。"""
    set_events, captured = fake_stream
    set_events([{"type": "token", "text": "ok"}, {"type": "done"}])
    conv = db.create_conversation("旧会话", device_id=device_id)
    db.upsert_channel_binding("wecom", "nl_u", conv_id=conv["id"], device_id=device_id)

    # 自然语言开新会话：重置会话绑定，不消耗编排器
    r1 = await channel_gateway.run_channel_message("wecom", "nl_u", "帮我开启一个新会话")
    assert "已开启新会话" in r1["replies"][0]
    assert db.get_channel_binding("wecom", "nl_u")["conv_id"] == ""
    assert captured.get("message") is None

    # 疑问句不拦截，正常走编排器
    r2 = await channel_gateway.run_channel_message("wecom", "nl_u", "如何新建会话？")
    assert captured["message"] == "如何新建会话？"
    assert r2["conv_id"] and r2["conv_id"] != conv["id"]

    # 自然语言切换设备：仅变更绑定并重置会话（切换动作本身不连接设备）
    r3 = await channel_gateway.run_channel_message("wecom", "nl_u", f"把设备切换到{device_id}")
    assert "已切换" in r3["replies"][0] and "新会话" in r3["replies"][0]
    b = db.get_channel_binding("wecom", "nl_u")
    assert b["device_id"] == device_id and b["conv_id"] == ""

    # 目标缺省的「切换设备」返回设备列表与用法提示
    r4 = await channel_gateway.run_channel_message("wecom", "nl_u", "切换设备")
    assert "设备列表" in r4["replies"][0] and "切换设备 关键词" in r4["replies"][0]
    assert captured["message"] == "如何新建会话？"   # 全程未再消耗编排器
