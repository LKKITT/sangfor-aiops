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


class ChatIn(BaseModel):
    message: str
    device_id: str


class ConfirmIn(BaseModel):
    action_id: str
    device_id: str
    approved: bool


def _sse_events(generator):
    async def event_stream():
        try:
            async for event in generator:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except guardrails.GuardrailError as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        except Exception as e:   # noqa: BLE001
            yield f"data: {json.dumps({'type': 'error', 'text': f'服务异常：{e}'}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("")
async def chat(payload: ChatIn):
    if not db.get_device(payload.device_id):
        raise HTTPException(404, "设备不存在")
    conv = db.create_conversation(payload.message[:40])
    return _sse_events(orchestrator.stream_chat(conv["id"], payload.message, payload.device_id))


@router.post("/confirm")
async def confirm(payload: ConfirmIn):
    action = db.get_pending_action(payload.action_id)
    if not action:
        raise HTTPException(404, "确认任务不存在")
    return _sse_events(orchestrator.resume_confirm(action["conv_id"], payload.action_id,
                                                   payload.approved, payload.device_id))


@router.get("/conversations")
def conversations() -> list[dict]:
    return db.list_conversations()


@router.get("/conversations/{conv_id}")
def conversation_messages(conv_id: str) -> list[dict]:
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, "会话不存在")
    return db.get_messages(conv_id)


@router.get("/audit")
def audit_logs() -> list[dict]:
    return db.list_audit(limit=100)
