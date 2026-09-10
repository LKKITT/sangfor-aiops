"""对话 API：SSE 流式对话 + 变更确认 + 会话管理。"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import db
from app.agent import guardrails
from app.agent.orchestrator import AgentOrchestrator

router = APIRouter(prefix="/api/chat", tags=["chat"])
orchestrator = AgentOrchestrator()

# 活跃对话取消信号：conv_id → Event
import asyncio
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


@router.post("")
async def chat(payload: ChatIn):
    if not db.get_device(payload.device_id):
        raise HTTPException(404, "设备不存在")
    # 续接已有对话（含待确认卡片上下文）或开启新对话
    conv_id = payload.conv_id
    if conv_id and db.get_conversation(conv_id):
        db.touch_conversation(conv_id, title=payload.message)
    else:
        conv = db.create_conversation(payload.message[:40])
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
def conversation_detail_list() -> list[dict]:
    """返回对话列表（含摘要、消息数、设备名）。"""
    convs = db.list_conversations(limit=100)
    devices = {d["id"]: d["name"] for d in db.list_devices()}
    out = []
    for c in convs:
        msgs = db.get_messages(c["id"])
        summary = db.get_conv_summary(c["id"])
        user_msgs = [m for m in msgs if m["role"] == "user"]
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        # 从记忆摘要或对话历史提取设备
        device_name = ""
        for m in msgs:
            t = m.get("content", {}).get("text", "")
            for did, dname in devices.items():
                if did in t or dname in t:
                    device_name = dname
                    break
            if device_name:
                break
        out.append({
            "id": c["id"],
            "title": c["title"],
            "created_at": c["created_at"],
            "updated_at": c["updated_at"],
            "msg_count": len(msgs),
            "user_msg_count": len(user_msgs),
            "assistant_msg_count": len(assistant_msgs),
            "summary": (summary or {}).get("summary", ""),
            "device_name": device_name,
            "last_message": user_msgs[-1]["content"].get("text", "")[:100] if user_msgs else "",
        })
    return out


@router.get("/conversations/{conv_id}")
def conversation_messages(conv_id: str) -> list[dict]:
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    return db.get_messages(conv_id)


@router.get("/audit")
def audit_logs() -> list[dict]:
    return db.list_audit(limit=100)
