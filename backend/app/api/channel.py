"""外部渠道 API：企微机器人连接状态查询 + 渠道消息调试入口。

POST /api/channel/message 可在不依赖企微服务端的情况下直接驱动渠道网关
（联调渠道对话逻辑、验证白名单/确认流），与企微长连接共用同一处理链路。
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.services import channel_gateway, wecom_bot_service

router = APIRouter(prefix="/api/channel", tags=["channel"])


class ChannelMessageIn(BaseModel):
    channel: str = "wecom"      # 渠道标识（wecom，预留扩展）
    sender_id: str              # 渠道内发送方唯一标识（企微 userid）
    text: str


class ChannelConfirmIn(BaseModel):
    channel: str = "wecom"
    sender_id: str
    action_id: str
    approved: bool


@router.get("/wecom/status")
def wecom_status() -> dict:
    """企微智能机器人长连接状态（供 Web 设置页/监控展示）。"""
    return wecom_bot_service.status()


@router.post("/message")
async def channel_message(payload: ChannelMessageIn) -> dict:
    """调试入口：模拟渠道消息进入网关，返回与渠道适配器一致的回复结构。"""
    return await channel_gateway.run_channel_message(payload.channel, payload.sender_id,
                                                     payload.text)


@router.post("/confirm")
async def channel_confirm(payload: ChannelConfirmIn) -> dict:
    """调试入口：模拟渠道内确认/拒绝操作。"""
    return await channel_gateway.run_channel_confirm(payload.channel, payload.sender_id,
                                                     payload.action_id, payload.approved)
