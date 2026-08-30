"""Agent 安全护栏：只读模式、敏感操作黑名单、变更审计。"""
import re

from app import db
from app.config import settings

# ---- 敏感操作黑名单（无论工具如何包装，命中即拒绝） ----
BLACKLIST_PATTERNS = [
    (re.compile(r"恢复出厂|出厂设置|factory\s*reset", re.I), "恢复出厂设置会清空全部业务配置，Agent 禁止执行该操作"),
    (re.compile(r"删除(所有|全部|全部的)?管理员|删除admin账号|重置管理员", re.I), "删除/重置管理员账号会导致设备失管，Agent 禁止执行该操作"),
    (re.compile(r"关闭(高可用|HA|双机)|禁用HA", re.I), "关闭高可用会造成单点风险，请由工程师在维护窗口手工操作，Agent 禁止执行"),
    (re.compile(r"清空(配置|策略|所有策略)|一键清空", re.I), "批量清空配置属于高危操作，Agent 禁止执行；请逐条确认后操作"),
]
# 升级动作只建议、不代执行（升级会中断业务，须人工窗口执行）
UPGRADE_EXEC_PATTERN = re.compile(r"(帮我|直接|现在)(执行|做|升级|刷)升?级|升级(吧|包安装|固件)", re.I)

FORBIDDEN_RESOURCES = {"admin", "account", "factory", "ha", "firmware"}


class GuardrailError(Exception):
    """护栏拦截错误，message 直接面向用户。"""


def check_user_request(message: str) -> None:
    """对用户消息做黑名单检查（在 LLM 之前拦截，双保险）。"""
    for pattern, reason in BLACKLIST_PATTERNS:
        if pattern.search(message):
            raise GuardrailError(reason)


def check_tool_call(tool_name: str, args: dict, device: dict | None) -> None:
    """对 LLM 发起的工具调用做检查。"""
    meta = TOOLS_META.get(tool_name)
    if meta is None:
        raise GuardrailError(f"未知工具：{tool_name}")
    # 资源黑名单
    resource = str(args.get("resource", "")).lower()
    if resource in FORBIDDEN_RESOURCES:
        raise GuardrailError(f"资源 {resource} 属于敏感操作，禁止通过 Agent 变更")
    # 写操作：只读模式拦截
    if meta.get("write"):
        if settings.readonly_mode:
            raise GuardrailError("系统处于只读模式（READONLY_MODE=true），所有变更类操作已被禁止")
        if device and device.get("readonly"):
            raise GuardrailError(f"设备「{device.get('name')}」处于只读模式，变更被拒绝")
    # 升级代执行拦截
    if UPGRADE_EXEC_PATTERN.search(str(args.get("intent", ""))):
        raise GuardrailError("升级操作涉及业务中断，需人工在维护窗口执行；Agent 仅提供升级建议与前置检查清单")


def audit_tool(tool_name: str, args: dict, result: str, conv_id: str = "", device_id: str = "") -> None:
    db.audit(f"agent.tool.{tool_name}", {"args": args}, conv_id=conv_id, device_id=device_id, result=result)


# 工具元数据由 tools.py 注册时写入
TOOLS_META: dict[str, dict] = {}


def register_meta(name: str, meta: dict) -> None:
    TOOLS_META[name] = meta
