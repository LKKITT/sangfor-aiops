"""企业微信智能机器人渠道：WebSocket 长连接接入（官方 wecom-aibot SDK）。

基于 BotID + Secret 订阅 wss://openws.work.weixin.qq.com，SDK 内置 30s 心跳与
指数退避重连；本模块只做协议适配：收消息 → channel_gateway 编排 → 流式回复。

注意：每个机器人同一时刻仅允许一条有效长连接（新连接会踢掉旧连接），
uvicorn --reload 的多进程模式会导致互踢，生产请单进程运行。
"""
import asyncio
import json
import logging
import re
from collections import deque
from datetime import datetime

from app.config import settings
from app.services import app_settings, channel_gateway

log = logging.getLogger("sangfor-agent.wecom")

try:
    from aibot import WSClient, WSClientOptions
    from aibot.utils import generate_req_id
    SDK_AVAILABLE = True
except ImportError:   # 未安装 SDK 时后端仍可正常启动（仅企微渠道不可用）
    SDK_AVAILABLE = False

CHANNEL = "wecom"
_action_id_re = re.compile(r"act_[0-9a-f]{6,}")

_client = None            # WSClient 实例
_run_task = None          # 连接保活任务
_stop_event: asyncio.Event | None = None
_state = {
    "status": "disabled",       # disabled / connecting / connected / reconnecting / error
    "bot_id": "",
    "last_error": "",
    "connected_at": "",
}
_recent_msgids: deque = deque(maxlen=256)


def status() -> dict:
    cfg = app_settings.get_wecom_config()
    return dict(_state, sdk_available=SDK_AVAILABLE, readonly_mode=settings.channel_readonly,
                enabled=cfg["enabled"], config_source=cfg["source"])


def _set_status(s: str, err: str = "") -> None:
    _state["status"] = s
    if err:
        _state["last_error"] = err


async def start() -> None:
    """lifespan 启动：未启用或未配置凭据时静默跳过（保持既有部署零影响）。

    配置来源：界面『平台设置』(DB) 优先，.env 兜底（app_settings.get_wecom_config）。
    """
    global _client, _run_task, _stop_event
    cfg = app_settings.get_wecom_config()
    if not cfg["enabled"]:
        _set_status("disabled")
        return
    if not SDK_AVAILABLE:
        _set_status("error", "wecom-aibot-python-sdk 未安装（pip install wecom-aibot-python-sdk）")
        log.warning("企微渠道启用但 SDK 未安装，%s", _state["last_error"])
        return
    if not cfg["bot_id"] or not cfg["secret"]:
        _set_status("error", "缺少机器人 BotID / Secret 配置（平台设置或 .env）")
        log.warning("企微渠道启用但凭据缺失，%s", _state["last_error"])
        return

    _set_status("connecting")
    _state["bot_id"] = cfg["bot_id"]
    _client = WSClient(WSClientOptions(
        bot_id=cfg["bot_id"],
        secret=cfg["secret"],
        max_reconnect_attempts=-1,   # 无限重连，交给 lifespan 管理生命周期
    ))
    _register_handlers(_client)
    _stop_event = asyncio.Event()
    _run_task = asyncio.create_task(_run_client())
    log.info("企微智能机器人渠道已启动 bot=%s（配置来源 %s）",
             _mask(cfg["bot_id"]), cfg["source"])


async def apply_config() -> None:
    """平台设置保存后热更新：断开旧连接并按新配置重启（未启用则保持停止）。"""
    await stop()
    await start()


async def stop() -> None:
    global _client, _run_task
    if _run_task:
        if _stop_event:
            _stop_event.set()
        _run_task.cancel()
        try:
            await _run_task
        except (asyncio.CancelledError, Exception):   # noqa: BLE001
            pass
        _run_task = None
    if _client:
        try:
            _client.disconnect()
        except Exception:   # noqa: BLE001
            pass
        _client = None
    _set_status("disabled")


def _mask(v: str) -> str:
    return v[:6] + "***" if len(v) > 6 else "***"


async def _run_client() -> None:
    try:
        await _client.connect()
        if _stop_event:
            await _stop_event.wait()
    except asyncio.CancelledError:
        raise
    except Exception as e:   # noqa: BLE001
        _set_status("error", str(e))
        log.error("企微长连接异常退出：%s", e)


def _register_handlers(client) -> None:
    def _safe(handler):
        async def wrapper(frame: dict):
            try:
                await handler(frame)
            except Exception:   # noqa: BLE001 —— 处理器异常不允许打断长连接
                log.exception("企微渠道处理器异常")
        return wrapper

    client.on("authenticated", lambda: _on_authenticated())
    client.on("disconnected", lambda reason: _on_disconnected(reason))
    client.on("reconnecting", lambda attempt: _on_reconnecting(attempt))
    client.on("error", lambda e: _set_status("error", str(e)))
    client.on("message.text", _safe(_on_text))
    for t in ("message.image", "message.voice", "message.file", "message.mixed"):
        client.on(t, _safe(lambda frame, _t=t: _reply_markdown(
            frame, "暂不支持图片/语音/文件消息，请直接发送文字。")))
    client.on("event.enter_chat", _safe(_on_enter_chat))
    client.on("event.template_card_event", _safe(_on_card_event))


def _on_authenticated() -> None:
    _set_status("connected")
    _state["connected_at"] = datetime.now().isoformat(timespec="seconds")
    _state["last_error"] = ""
    log.info("企微智能机器人已认证上线")


def _on_disconnected(reason: str) -> None:
    _set_status("reconnecting", reason)
    log.warning("企微长连接断开：%s（SDK 自动重连中）", reason)


def _on_reconnecting(attempt: int) -> None:
    _set_status("reconnecting", f"第 {attempt} 次重连")
    log.info("企微长连接重连中（第 %s 次）", attempt)


def _dedup(msgid: str) -> bool:
    """True=重复消息（忽略）。SDK 不做排重，这里按 msgid 滑动窗口去重。"""
    if not msgid:
        return False
    if msgid in _recent_msgids:
        return True
    _recent_msgids.append(msgid)
    return False


async def _on_text(frame: dict) -> None:
    body = frame.get("body", {})
    if _dedup(body.get("msgid", "")):
        return
    userid = (body.get("from") or {}).get("userid", "")
    content = ((body.get("text") or {}).get("content") or "").strip()
    chattype = body.get("chattype", "single")
    if not userid or not content:
        return
    if chattype == "group":
        await _reply_markdown(frame, "群聊支持即将上线，请先通过单聊使用。")
        return
    asyncio.create_task(_handle_text(frame, userid, content))


async def _handle_text(frame: dict, userid: str, content: str) -> None:
    """单条消息的完整处理：先回「正在处理」占位，聚合完编排器结果后流式收尾。"""
    if _client is None:   # 停服瞬间的在途消息直接丢弃
        return
    stream_id = generate_req_id("stream")
    try:
        await _client.reply_stream(frame, stream_id, "正在处理，请稍候…", finish=False)
    except Exception as e:   # noqa: BLE001 —— 占位帧失败不阻断正式回复
        log.warning("流式占位帧发送失败：%s", e)

    result = await channel_gateway.run_channel_message(CHANNEL, userid, content)
    replies = result.get("replies") or ["（无回复内容）"]
    try:
        await _client.reply_stream(frame, stream_id, "\n\n".join(replies), finish=True)
    except Exception as e:   # noqa: BLE001
        log.error("企微回复发送失败：%s", e)
        return

    # 非只读模式下为待确认变更补发按钮卡片（卡片点击即可确认/拒绝）
    pending = result.get("pending")
    if pending and not settings.channel_readonly:
        try:
            await _client.send_message(userid, {
                "msgtype": "template_card",
                "template_card": _confirm_card(pending["action_id"], pending["summary"]),
            })
        except Exception as e:   # noqa: BLE001 —— 卡片失败不影响文本指令确认
            log.warning("确认卡片发送失败（可回复文本指令确认）：%s", e)


def _confirm_card(action_id: str, summary: str) -> dict:
    return {
        "card_type": "button_interaction",
        "source": {"desc": "深信服运维助手", "desc_color": 1},
        "task_id": action_id,
        "main_title": "待确认变更",
        "sub_title_text": (summary or "变更操作")[:120],
        "button_list": [
            {"text": "确认执行", "type": 1, "key": f"confirm:{action_id}"},
            {"text": "拒绝", "type": 2, "key": f"cancel:{action_id}"},
        ],
    }


async def _on_enter_chat(frame: dict) -> None:
    """用户当天首次进入单聊：5 秒窗口内回复欢迎语。"""
    if _client is None:
        return
    try:
        await _client.reply_welcome(frame, {
            "msgtype": "text", "text": {"content": channel_gateway.HELP_TEXT}})
    except Exception as e:   # noqa: BLE001
        log.warning("欢迎语发送失败：%s", e)


async def _on_card_event(frame: dict) -> None:
    """模板卡片按钮点击：解析动作并确认执行；5 秒窗口内先更新卡片状态。"""
    if _client is None:
        return
    event_body = frame.get("body", {}).get("event", {})
    raw = json.dumps(event_body, ensure_ascii=False)
    approved = True if "confirm:" in raw else (False if "cancel:" in raw else None)
    m = _action_id_re.search(raw)
    userid = ""
    for candidate in (event_body.get("from") or {}, frame.get("body", {}).get("from") or {}):
        userid = candidate.get("userid", "")
        if userid:
            break
    if approved is None or not m:
        return
    action_id = m.group(0)

    if settings.channel_readonly:
        await _try_update_card(frame, action_id, "当前渠道为只读模式，请在 Web 控制台执行变更")
        return

    # 先在 5 秒窗口内更新卡片为「已受理」，执行结果随后主动推送
    await _try_update_card(frame, action_id,
                           "已受理，正在执行变更…" if approved else "已受理，正在取消变更…")
    result = await channel_gateway.run_channel_confirm(CHANNEL, userid, action_id, approved)
    if not userid:   # 拿不到发送方时无法主动推送，结果仅记录日志
        log.warning("卡片事件缺少 userid，确认结果无法推送 action=%s", action_id)
        return
    text = "\n\n".join(result.get("replies") or ["完成。"])
    try:
        await _client.send_message(userid, {"msgtype": "markdown", "markdown": {"content": text}})
    except Exception as e:   # noqa: BLE001
        log.error("确认结果推送失败：%s", e)


async def _try_update_card(frame: dict, action_id: str, tip: str) -> None:
    try:
        await _client.update_template_card(frame, {
            "card_type": "button_interaction",
            "task_id": action_id,
            "main_title": "变更确认",
            "sub_title_text": tip,
            "button_list": [{"text": "已处理", "type": 2, "key": "done",
                             "replace_text": tip[:40]}],
        })
    except Exception as e:   # noqa: BLE001 —— 卡片更新超时/格式不符仅记录，不影响主流程
        log.warning("卡片状态更新失败：%s", e)


async def _reply_markdown(frame: dict, text: str) -> None:
    if _client is None:
        return
    try:
        await _client.reply(frame, {"msgtype": "markdown", "markdown": {"content": text}})
    except Exception as e:   # noqa: BLE001
        log.warning("企微 markdown 回复失败：%s", e)
