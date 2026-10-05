"""对话 API：SSE 流式对话 + 变更确认 + 会话管理。"""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import db
from app.agent import guardrails
from app.agent.orchestrator import AgentOrchestrator
from app.services import device_scope

router = APIRouter(prefix="/api/chat", tags=["chat"])
orchestrator = AgentOrchestrator()

# 活跃对话取消信号：conv_id → Event
_cancel_events: dict[str, asyncio.Event] = {}


class ChatIn(BaseModel):
    message: str
    device_id: str
    conv_id: str | None = None   # 续接已有对话（带待确认卡片上下文）
    use_knowledge: bool = False  # 勾选后启用官方知识库检索技能（诸葛小T）


class ConfirmIn(BaseModel):
    action_id: str
    device_id: str
    approved: bool
    edited: dict | None = None   # 用户在确认卡片上编辑后的参数（如绑定表单）


def _sse_events(generator, conv_id: str = ""):
    async def event_stream():
        cancel_event = _cancel_events.get(conv_id)
        try:
            async for event in generator:
                if cancel_event and cancel_event.is_set():
                    yield f"data: {json.dumps({'type': 'cancelled'}, ensure_ascii=False)}\n\n"
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    return
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except guardrails.GuardrailError as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        except Exception as e:   # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'text': f'服务异常：{e}'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        finally:
            if conv_id and conv_id in _cancel_events:
                _cancel_events.pop(conv_id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _device_exists(device_id: str) -> bool:
    """设备存在性校验（统一走 device_scope：global / nd_ 网络设备 / 深信服设备）。"""
    return device_scope.exists(device_id)


@router.post("")
async def chat(payload: ChatIn):
    if not _device_exists(payload.device_id):
        raise HTTPException(404, "设备不存在")
    # 续接已有对话（含待确认卡片上下文）或开启新对话
    conv_id = payload.conv_id
    if conv_id and db.get_conversation(conv_id):
        db.touch_conversation(conv_id, title=payload.message, device_id=payload.device_id)
    else:
        conv = db.create_conversation(payload.message[:40], device_id=payload.device_id)
        conv_id = conv["id"]
    # 注册取消信号
    _cancel_events[conv_id] = asyncio.Event()
    return _sse_events(orchestrator.stream_chat(conv_id, payload.message, payload.device_id,
                                                use_knowledge=payload.use_knowledge), conv_id=conv_id)


@router.post("/{conv_id}/cancel")
async def cancel_chat(conv_id: str):
    """取消正在进行的对话。"""
    event = _cancel_events.get(conv_id)
    if not event:
        raise HTTPException(404, "没有找到正在进行的对话或对话已结束")
    event.set()
    return {"ok": True, "message": "对话已取消"}


@router.post("/confirm")
async def confirm(payload: ConfirmIn):
    action = db.get_pending_action(payload.action_id)
    if not action:
        raise HTTPException(404, "确认任务不存在")
    return _sse_events(orchestrator.resume_confirm(action["conv_id"], payload.action_id,
                                                   payload.approved, payload.device_id,
                                                   edited=payload.edited))


@router.get("/conversations")
def conversations() -> list[dict]:
    return db.list_conversations()


@router.get("/conversations/detail")
def conversation_detail_list(page: int = 1, page_size: int = 20, keyword: str = "",
                             device_id: str = "", start: str = "", end: str = "") -> dict:
    """分页会话列表（含消息数/最后消息/摘要/设备名），支持关键词/设备/时间范围筛选。"""
    return db.list_conversations_paged(page=page, page_size=page_size, keyword=keyword,
                                       device_id=device_id, start=start, end=end)


@router.get("/conversations/{conv_id}")
def conversation_messages(conv_id: str, limit: int = 200, before_id: int | None = None) -> list[dict]:
    """会话消息（按 id 升序）。默认返回最近 200 条（上限 1000）；before_id 供前端向上翻页。"""
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    return db.get_messages(conv_id, limit=max(1, min(limit, 1000)), before_id=before_id)


@router.get("/last-conversation/{device_id}")
def last_conversation(device_id: str) -> dict:
    """该设备最近一次会话：消息历史 + 待确认动作（供前端切换菜单后恢复对话）。"""
    conv = db.latest_conversation_by_device(device_id)
    if not conv:
        return {"conv_id": None, "messages": [], "pending_action": None}
    pending = db.get_pending_action_by_conv(conv["id"])
    return {
        "conv_id": conv["id"],
        "messages": db.get_messages(conv["id"], limit=60),
        "pending_action": {
            "action_id": pending["id"], "tool_name": pending["tool_name"],
            "summary": pending["summary"], "status": pending["status"],
        } if pending and pending["status"] == "pending" else None,
    }


@router.get("/audit")
def audit_logs() -> list[dict]:
    return db.list_audit(limit=100)
