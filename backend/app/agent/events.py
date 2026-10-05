"""Agent SSE 事件契约（前后端共享协议的单一来源）。

orchestrator 产出的事件即本模块模型的序列化结果；schema 导出为
docs/sse-events.schema.json（测试守卫漂移），前端 chat/agentStream.js 按此消费。
新增/修改事件字段时：先改本模块 → 重跑 scripts/gen_sse_schema.py → 同步前端。
"""
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class MetaEvent(BaseModel):
    type: Literal["meta"]
    conv_id: str
    device_id: str = ""


class SkillSelectedEvent(BaseModel):
    type: Literal["skill_selected"]
    skill: str
    name: str


class TokenEvent(BaseModel):
    type: Literal["token"]
    text: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"]
    name: str
    args: dict = Field(default_factory=dict)


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"]
    name: str
    preview: str = ""


class ConfirmRequiredEvent(BaseModel):
    type: Literal["confirm_required"]
    action: dict


class ConfirmResultEvent(BaseModel):
    type: Literal["confirm_result"]
    action_id: str
    approved: bool
    failed: bool = False
    result: Any = None
    safety_backup_id: str = ""   # 变更前自动安全备份（前端据此渲染"查看回退点"）


class OfflineNoticeEvent(BaseModel):
    type: Literal["offline_notice"]
    text: str


class CancelledEvent(BaseModel):
    type: Literal["cancelled"]


class ErrorEvent(BaseModel):
    type: Literal["error"]
    text: str


class DoneEvent(BaseModel):
    type: Literal["done"]


AgentEvent = Union[
    MetaEvent, SkillSelectedEvent, TokenEvent, ToolCallEvent, ToolResultEvent,
    ConfirmRequiredEvent, ConfirmResultEvent, OfflineNoticeEvent,
    CancelledEvent, ErrorEvent, DoneEvent,
]
