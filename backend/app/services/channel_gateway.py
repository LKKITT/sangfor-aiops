"""外部消息渠道网关：企微/飞书/QQ 等渠道共用的编排器桥接层（渠道无关）。

职责：发送方白名单 → 渠道绑定（会话/设备）→ 管理指令短路 → 调用编排器并聚合
事件流为纯文本回复。编排器（AgentOrchestrator）零改动复用，Web SSE 渠道不受影响。

回复为分段的纯 Markdown 文本列表，由各渠道适配器负责发送（如企微流式消息）。
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import AsyncGenerator, Optional

from app import db
from app.agent.orchestrator import AgentOrchestrator
from app.config import settings

log = logging.getLogger("sangfor-agent.channel")

orchestrator = AgentOrchestrator()   # 独立实例：状态全在 DB，与 Web SSE 实例互不影响

# 单条回复分段上限（企微 markdown 消息长度有限，超长拆分多条）
SEGMENT_LIMIT = 1500
MAX_SEGMENTS = 4

HELP_TEXT = (
    "**深信服运维助手**（企微渠道）\n"
    "直接发消息即可对话，支持：设备状态 / 配置体检 / 备份与恢复 / 升级建议 / 知识问答。\n"
    "管理指令（自然语言表达也可以，如\"开个新会话\"\"切换到演示设备\"）：\n"
    "- 设备列表 — 查看可管理的设备\n"
    "- 切换设备 关键词 — 切换当前操作设备（如：切换设备 演示）\n"
    "- 当前设备 — 查看当前操作设备\n"
    "- 新会话 — 开启新会话（对话日志独立成条）\n"
    "- 帮助 — 显示本说明"
)

_confirm_re = re.compile(r"^(确认|取消|approve|reject)\s*[:：#\s]*([A-Za-z0-9_\-]+)\s*$",
                         re.IGNORECASE)
# 自然语言意图（短文本、非疑问句才视为指令，避免误伤正常提问）：
# 开新会话：新会话/新开会话/新的对话/重新开始/重置会话/清空上下文 等
_new_session_re = re.compile(
    r"新[^，。,\s]{0,2}(?:对话|会话)|重新开始|重置(?:对话|会话)|清空(?:上下文|会话|对话)")
# 切换设备：切换(设备)?(到|至|成|为) 目标 —— 兼容「切换设备 X」「切换到X」「把设备切换到X」；
# 目标必须带显式标记（开头「切换…」或「切换到/至/成/为」），避免「查看主备切换记录」被误判
_switch_to_re = re.compile(r"切(?:换)?(?:设备)?(?:到|至|成|为)\s*[:：#\s]*([0-9A-Za-z\u4e00-\u9fff\-_.]+)")
_switch_prefix_re = re.compile(r"^切换(?:设备)?\s*[:：#\s]*([0-9A-Za-z\u4e00-\u9fff\-_.]+)")
# 疑问句特征：含这些词的交给 LLM 正常回答，不做指令短路
_question_words = ("如何", "怎么", "怎样", "什么", "哪些", "吗", "？", "?")
# 渠道发送方锁：同一发送方串行处理，避免同一会话内消息交叉消耗 LLM 轮次
_locks: dict[tuple, asyncio.Lock] = {}


def _sender_lock(channel: str, sender_id: str) -> asyncio.Lock:
    lock = _locks.get((channel, sender_id))
    # pytest 等场景下事件循环可能更换：锁绑定旧循环时重建，避免跨循环复用报错
    if lock is None or getattr(lock, "_loop", None) not in (asyncio.get_running_loop(), None):
        lock = asyncio.Lock()
        _locks[(channel, sender_id)] = lock
    return lock


def sender_allowed(channel: str, sender_id: str) -> bool:
    """发送方白名单：目前仅企微渠道可配（WECOM_ALLOWED_USERS，逗号分隔，空=允许全部）。"""
    if channel == "wecom":
        raw = (settings.wecom_allowed_users or "").strip()
        if not raw:
            return True
        return sender_id in {u.strip() for u in raw.split(",") if u.strip()}
    return True


def parse_confirm_command(text: str) -> Optional[tuple[bool, str]]:
    """识别「确认/取消 <action_id>」文本指令；非确认指令返回 None。"""
    m = _confirm_re.match((text or "").strip())
    if not m:
        return None
    return (m.group(1).lower() in ("确认", "approve"), m.group(2))


def _is_question_like(t: str) -> bool:
    return any(k in t for k in _question_words)


def _is_new_session_text(t: str) -> bool:
    """自然语言开新会话意图（如「帮我开启一个新会话」）；疑问句除外。"""
    return len(t) <= 20 and not _is_question_like(t) and bool(_new_session_re.search(t))


def _parse_switch_keyword(t: str) -> Optional[str]:
    """自然语言切换设备意图，返回目标关键词；非切换指令返回 None。

    兼容「切换设备 演示」「切换到AC」「把设备切换到AF模拟器」等表达；
    仅做字符串匹配，不触碰设备连接（切换本身无需检测连通性）。
    """
    if not t or len(t) > 30 or _is_question_like(t):
        return None
    m = _switch_to_re.search(t) or _switch_prefix_re.match(t)
    if not m:
        return None
    keyword = m.group(1).strip()
    # 去掉黏在目标上的口语/标记尾缀（「切换一下设备」→「一下」→ 空 = 无有效目标）
    prev = None
    while prev != keyword:
        prev = keyword
        keyword = re.sub(r"(?:设备|一下|下|到|至|成|为)$", "", keyword).strip()
    return keyword or None


def _split_reply(text: str) -> list[str]:
    """按上限分段（优先在换行处断开），超段数截断并提示到 Web 查看。"""
    if len(text) <= SEGMENT_LIMIT:
        return [text] if text else []
    segments: list[str] = []
    rest = text
    while rest and len(segments) < MAX_SEGMENTS:
        if len(rest) <= SEGMENT_LIMIT:
            segments.append(rest)
            break
        cut = rest.rfind("\n", SEGMENT_LIMIT // 2, SEGMENT_LIMIT)
        if cut <= 0:
            cut = SEGMENT_LIMIT
        segments.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        segments[-1] += f"\n\n（内容过长已截断，完整内容请在 Web 控制台查看）"
    return segments


async def _consume_events(generator: AsyncGenerator[dict, None]) -> dict:
    """把编排器事件流聚合为纯文本回复 + 待确认动作信息。"""
    parts: list[str] = []
    pending: Optional[dict] = None
    error: str = ""
    async for ev in generator:
        etype = ev.get("type")
        if etype == "token":
            parts.append(ev.get("text", ""))
        elif etype == "confirm_required":
            action = ev.get("action") or {}
            pending = {"action_id": action.get("action_id", ""),
                       "tool_name": action.get("tool_name", ""),
                       "summary": action.get("title") or action.get("summary") or "变更操作"}
            break   # 编排器产出确认卡后即暂停
        elif etype == "error":
            error = ev.get("text", "服务异常")
        elif etype == "offline_notice":
            parts.append("\n\n" + ev.get("text", ""))
        elif etype == "done":
            break
    return {"reply": "".join(parts).strip(), "pending": pending, "error": error}


def _default_device(devices: list[dict]) -> dict:
    """默认设备：优先演示环境（内置模拟器），无模拟器时取第一台。"""
    sims = [d for d in devices if d.get("mode") == "simulator"]
    return (sims or devices)[0]


def _resolve_device(binding: dict) -> tuple[Optional[dict], str]:
    """解析当前操作设备：绑定值优先，失效/为空回退默认演示设备（并回写绑定）。"""
    devices = db.list_devices()
    if not devices:
        return None, ""
    dev = db.get_device(binding.get("device_id", "")) if binding.get("device_id") else None
    if dev is None:
        dev = _default_device(devices)
        db.upsert_channel_binding(binding["channel"], binding["sender_id"],
                                  device_id=dev["id"])
    return dev, dev["id"]


def _session_expired(binding: dict) -> bool:
    """超过会话超时未对话 → 下次消息自动开启新会话（超时为 0 或无记录时关闭）。"""
    timeout_min = settings.channel_session_timeout_min
    last = binding.get("last_active_at") or ""
    if timeout_min <= 0 or not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return False
    return (datetime.now() - last_dt).total_seconds() > timeout_min * 60


def _fmt_devices(current_id: str) -> str:
    type_names = {"af": "AF", "ac": "AC", "scp": "SCP", "hci": "HCI"}
    lines = []
    for i, d in enumerate(db.list_devices(), 1):
        mark = "▶" if d["id"] == current_id else " "
        mode = "模拟器" if d.get("mode") == "simulator" else "真实"
        lines.append(f"{mark} {i}. {d['name']}（{type_names.get(d.get('type', ''), d.get('type', ''))}"
                     f"·{mode}）")
    return "\n".join(lines)


def _handle_manage_command(text: str, channel: str, sender_id: str,
                           binding: dict) -> Optional[list[str]]:
    """短路处理管理指令；非管理指令返回 None。"""
    t = (text or "").strip()
    if t in ("帮助", "help", "Help", "HELP", "菜单"):
        return [HELP_TEXT]
    if t in ("设备列表", "设备清单", "我的设备"):
        current = binding.get("device_id") or (db.list_devices() or [{}])[0].get("id", "")
        return [f"**设备列表**\n{_fmt_devices(current)}\n\n回复「切换设备 关键词」切换操作设备"]
    if t in ("当前设备",):
        dev, _ = _resolve_device(binding)
        if not dev:
            return ["尚无可用设备，请先在 Web 控制台添加设备。"]
        return [f"当前操作设备：**{dev['name']}**\n回复「设备列表」查看全部，"
                f"「切换设备 关键词」切换。"]
    if t in ("新对话", "新会话", "重新开始", "重置会话") or _is_new_session_text(t):
        db.upsert_channel_binding(channel, sender_id, conv_id="")
        return ["已开启新会话，之前的会话上下文不再带入（对话日志按会话独立记录）。"]
    # 切换设备：纯绑定变更，不检测设备连通性（后续对话时才连接目标设备）
    if t in ("切换设备", "切换"):
        return [f"**设备列表**\n{_fmt_devices(binding.get('device_id', ''))}\n\n"
                f"回复「切换设备 关键词」切换，如：切换设备 演示"]
    keyword = _parse_switch_keyword(t)
    if keyword:
        devices = db.list_devices()
        hit = next((d for d in devices if keyword.lower() in (d["name"] or "").lower()
                    or keyword.lower() in d["id"].lower()), None)
        if hit is None:
            return [f"未找到匹配「{keyword}」的设备。\n**设备列表**\n"
                    f"{_fmt_devices(binding.get('device_id', ''))}"]
        # 切换同时重置会话：旧会话日志保持原设备归属，新对话在新设备下开启
        db.upsert_channel_binding(channel, sender_id, conv_id="", device_id=hit["id"])
        return [f"已切换到设备 **{hit['name']}** 并开启新会话，后续对话将针对该设备进行。"]
    return None


async def run_channel_message(channel: str, sender_id: str, text: str) -> dict:
    """渠道消息统一入口：返回 {ok, replies, conv_id, device_id, pending}。

    replies 为分段 Markdown 文本列表；pending 非 None 时表示产生了待确认变更。
    """
    if not sender_allowed(channel, sender_id):
        log.info("渠道消息被白名单拒绝 channel=%s sender=%s", channel, sender_id)
        return {"ok": False, "replies": ["抱歉，您暂未开通该机器人的使用权限。"],
                "conv_id": "", "device_id": "", "pending": None}
    lock = _sender_lock(channel, sender_id)
    if lock.locked():
        return {"ok": True, "replies": ["上一条消息还在处理中，请稍候再发送新消息…"],
                "conv_id": "", "device_id": "", "pending": None}
    async with lock:
        return await _run_message_locked(channel, sender_id, text)


async def _run_message_locked(channel: str, sender_id: str, text: str) -> dict:
    db.audit("channel.message", {"channel": channel, "text": (text or "")[:100]},
             actor=f"{channel}:{sender_id}")
    binding = db.get_channel_binding(channel, sender_id) or \
        db.upsert_channel_binding(channel, sender_id)

    # 确认指令：非只读模式开放「确认/取消 <id>」文本入口
    confirm = parse_confirm_command(text)
    if confirm:
        if settings.channel_readonly:
            return {"ok": True, "replies": ["当前渠道为只读模式，请在 Web 控制台的确认卡片中执行变更"],
                    "conv_id": binding["conv_id"], "device_id": binding["device_id"],
                    "pending": None}
        approved, action_id = confirm
        return await run_channel_confirm(channel, sender_id, action_id, approved)

    manage = _handle_manage_command(text, channel, sender_id, binding)
    if manage is not None:
        return {"ok": True, "replies": manage, "conv_id": binding["conv_id"],
                "device_id": binding["device_id"], "pending": None}

    dev, device_id = _resolve_device(binding)
    if dev is None:
        return {"ok": True, "replies": ["尚无可用设备，请先在 Web 控制台添加设备。"],
                "conv_id": "", "device_id": "", "pending": None}

    # 会话：绑定值有效则续接；长时间未对话自动开启新会话（日志按时间段分段）
    new_session_note = ""
    conv_id = binding.get("conv_id", "")
    if conv_id and not db.get_conversation(conv_id):
        conv_id = ""
    if conv_id and _session_expired(binding):
        conv_id = ""
        new_session_note = (f"（距上次对话已超过 {settings.channel_session_timeout_min} 分钟，"
                            f"已自动开启新会话）\n\n")
    if not conv_id:
        conv = db.create_conversation((text or "新对话")[:40], device_id=device_id)
        conv_id = conv["id"]
    db.upsert_channel_binding(channel, sender_id, conv_id=conv_id, device_id=device_id)

    try:
        result = await _consume_events(orchestrator.stream_chat(conv_id, text, device_id))
    except Exception as e:   # noqa: BLE001 —— 渠道侧兜底，不让异常逃出长连接服务
        log.exception("渠道对话处理失败 channel=%s sender=%s", channel, sender_id)
        return {"ok": False, "replies": [f"处理消息时出现异常：{e}"],
                "conv_id": conv_id, "device_id": device_id, "pending": None}

    reply = result["reply"] or result["error"]
    if result["pending"]:
        p = result["pending"]
        if settings.channel_readonly:
            reply += (f"\n\n⚠️ **待确认变更**：{p['summary']}\n"
                      f"当前渠道为只读模式，请到 Web 控制台打开该会话的确认卡片执行；"
                      f"或在配置中将 CHANNEL_READONLY_MODE 设为 false 后回复「确认 {p['action_id']}」。")
        else:
            reply += (f"\n\n⚠️ **待确认变更**：{p['summary']}\n"
                      f"回复「确认 {p['action_id']}」执行，「取消 {p['action_id']}」放弃。")
    if not reply.strip():
        reply = "（无回复内容）"
    return {"ok": True, "replies": _split_reply(new_session_note + reply), "conv_id": conv_id,
            "device_id": device_id, "pending": result["pending"]}


async def run_channel_confirm(channel: str, sender_id: str, action_id: str,
                              approved: bool) -> dict:
    """渠道内确认/拒绝变更：校验动作归属后走编排器 resume_confirm。"""
    binding = db.get_channel_binding(channel, sender_id)
    action = db.get_pending_action(action_id)
    if not action or not binding or action["conv_id"] != binding.get("conv_id"):
        return {"ok": False, "replies": ["确认任务不存在，或不属于当前会话，无法操作。"],
                "conv_id": "", "device_id": "", "pending": None}
    conv = db.get_conversation(action["conv_id"]) or {}
    device_id = binding.get("device_id") or conv.get("device_id", "")

    async def _run() -> dict:
        return await _consume_events(orchestrator.resume_confirm(
            action["conv_id"], action_id, approved, device_id))

    try:
        result = await _run()
    except Exception as e:   # noqa: BLE001
        log.exception("渠道确认处理失败 action=%s", action_id)
        return {"ok": False, "replies": [f"处理确认时出现异常：{e}"],
                "conv_id": action["conv_id"], "device_id": device_id, "pending": None}
    db.audit("channel.confirm", {"channel": channel, "action_id": action_id,
                                 "approved": approved},
             conv_id=action["conv_id"], device_id=device_id,
             actor=f"{channel}:{sender_id}",
             result="rejected" if not approved else "executed")
    head = "✅ 已确认执行" if approved else "🚫 已拒绝该变更"
    reply = result["reply"] or result["error"] or "完成。"
    return {"ok": True, "replies": _split_reply(f"{head}\n\n{reply}"),
            "conv_id": action["conv_id"], "device_id": device_id, "pending": None}
