"""系统设置 API：基本配置（BBS/LLM/企微）+ MCP 服务管理 + Agent Skills 管理（保存即生效）。"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app.config import settings
from app.services import agent_skills_service, app_settings, mcp_service, wecom_bot_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsIn(BaseModel):
    zhuge_bbs_username: str | None = None
    zhuge_bbs_password: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    wecom_aibot_enabled: bool | None = None
    wecom_aibot_id: str | None = None
    wecom_aibot_secret: str | None = None


def _zhuge_status() -> dict:
    user, _pwd, source = app_settings.get_zhuge_credentials()
    if source == "none":
        from app.services import zhuge_kb_service
        # 界面与 .env 均未配置时，技能内置账号仍可兜底（状态如实展示来源）
        source = "builtin" if zhuge_kb_service.has_builtin_credentials() else "none"
    return {
        "zhuge_username": user,
        "zhuge_password_set": source != "none",
        "zhuge_account_source": source,
        # 平台 Cookie 仅支持 .env 配置（软件列表等认证内容抓取用），界面不再提供输入
        "platform_cookie_configured": bool(settings.support_cookie),
    }


def _wecom_status() -> dict:
    cfg = app_settings.get_wecom_config()
    conn = wecom_bot_service.status()
    return {
        "wecom_enabled": cfg["enabled"],
        "wecom_bot_id": cfg["bot_id"],
        "wecom_secret_set": bool(cfg["secret"]),
        "wecom_source": cfg["source"],
        "wecom_conn_status": conn["status"],
        "wecom_conn_error": conn.get("last_error", ""),
    }


@router.get("")
def get_settings() -> dict:
    llm = app_settings.get_llm_config()
    return {
        **_zhuge_status(),
        "llm_base_url": llm["base_url"],
        "llm_model": llm["model"],
        "llm_api_key_set": bool(llm["api_key"]) and not llm["api_key"].startswith("your-"),
        "llm_source": llm["source"],
        "readonly_mode": settings.readonly_mode,
        "auto_backup_hour": settings.auto_backup_hour,
        **_wecom_status(),
    }


@router.post("")
async def save_settings(payload: SettingsIn) -> dict:
    if payload.zhuge_bbs_username is not None:
        db.set_setting(app_settings.ZHUGE_USER_KEY, payload.zhuge_bbs_username.strip())
    if payload.zhuge_bbs_password is not None:
        db.set_setting(app_settings.ZHUGE_PASS_KEY, payload.zhuge_bbs_password.strip())
    if payload.llm_base_url is not None:
        db.set_setting(app_settings.LLM_URL_KEY, payload.llm_base_url.strip())
    if payload.llm_api_key is not None and not payload.llm_api_key.startswith("***"):
        db.set_setting(app_settings.LLM_KEY_KEY, payload.llm_api_key.strip())
    if payload.llm_model is not None:
        db.set_setting(app_settings.LLM_MODEL_KEY, payload.llm_model.strip())
    wecom_updated = payload.wecom_aibot_enabled is not None or payload.wecom_aibot_id is not None \
        or payload.wecom_aibot_secret is not None
    if payload.wecom_aibot_enabled is not None:
        db.set_setting(app_settings.WECOM_ENABLED_KEY,
                       "true" if payload.wecom_aibot_enabled else "false")
    if payload.wecom_aibot_id is not None:
        db.set_setting(app_settings.WECOM_ID_KEY, payload.wecom_aibot_id.strip())
    if payload.wecom_aibot_secret is not None and not payload.wecom_aibot_secret.startswith("***"):
        db.set_setting(app_settings.WECOM_SECRET_KEY, payload.wecom_aibot_secret.strip())
    db.audit("settings.save", {
        "zhuge_account_updated": payload.zhuge_bbs_username is not None or payload.zhuge_bbs_password is not None,
        "llm_updated": payload.llm_api_key is not None or payload.llm_model is not None
                       or payload.llm_base_url is not None,
        "wecom_updated": wecom_updated,
    })
    if wecom_updated:
        await wecom_bot_service.apply_config()   # 长连接按新配置热重启（关闭则断开）
    return get_settings()


# ---------------- MCP 服务管理 ----------------

class McpServerIn(BaseModel):
    id: str | None = None
    name: str
    transport: str = "stdio"          # stdio / http
    command: str = ""
    args: list[str] = []
    env: dict = {}
    url: str = ""
    headers: dict = {}
    enabled: bool = False


class McpImportIn(BaseModel):
    text: str
    enabled: bool = False


class McpRegistryInstallIn(BaseModel):
    item: dict
    enabled: bool = True


@router.get("/mcp")
def mcp_list() -> dict:
    from app.agent.ext_tools import get_external_tools  # noqa: F401 —— 确认模块可导入
    return {"servers": mcp_service.get_servers(), "sdk_hint": mcp_service.MCP_SDK_HINT}


@router.put("/mcp")
async def mcp_save(payload: McpServerIn) -> dict:
    try:
        rec = mcp_service.upsert_server(payload.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    mcp_service.invalidate_tools_cache()   # 配置变更即生效（会话热重建）
    return {"ok": True, "server": rec}


@router.delete("/mcp/{server_id}")
def mcp_delete(server_id: str) -> dict:
    if not mcp_service.delete_server(server_id):
        raise HTTPException(404, "MCP 服务不存在")
    mcp_service.invalidate_tools_cache()
    return {"ok": True}


@router.post("/mcp/test")
async def mcp_test(payload: McpServerIn) -> dict:
    """连接测试：建立会话并列出工具（不落库）。"""
    try:
        rec = mcp_service._normalize(payload.model_dump())
        session = await mcp_service._get_session(rec)
        listing = await session.list_tools()
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, f"连接失败：{e}") from e
    return {"ok": True, "tools": [{"name": t.name,
                                   "description": (t.description or "")[:120]}
                                  for t in listing.tools]}


@router.post("/mcp/import")
def mcp_import(payload: McpImportIn) -> dict:
    try:
        return mcp_service.import_claude_json(payload.text, enabled=payload.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"JSON 解析失败：{e}") from e


@router.get("/mcp/registry")
async def mcp_registry(search: str = "", limit: int = 12) -> dict:
    try:
        return await mcp_service.registry_search(search, limit)
    except Exception as e:   # noqa: BLE001 —— 上游限流/网络问题友好透出
        raise HTTPException(502, f"注册表搜索失败：{e}") from e


@router.post("/mcp/registry/install")
def mcp_registry_install(payload: McpRegistryInstallIn) -> dict:
    try:
        rec = mcp_service.install_from_registry(payload.item, enabled=payload.enabled)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "server": rec}


# ---------------- Agent Skills 管理 ----------------

@router.get("/agent-skills")
def agent_skills_list() -> dict:
    return {"skills": agent_skills_service.list_skills()}


@router.post("/agent-skills/toggle")
def agent_skills_toggle(payload: dict) -> dict:
    folder = str(payload.get("folder") or "")
    if not folder:
        raise HTTPException(400, "folder 必填")
    agent_skills_service.toggle_skill(folder, bool(payload.get("enabled")))
    return {"ok": True}


@router.post("/agent-skills/import")
async def agent_skills_import(payload: dict) -> dict:
    try:
        return await agent_skills_service.import_from_github(
            str(payload.get("url") or ""), force=bool(payload.get("force")))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:   # noqa: BLE001
        raise HTTPException(502, f"导入失败：{e}") from e


@router.delete("/agent-skills/{folder}")
def agent_skills_delete(folder: str) -> dict:
    try:
        agent_skills_service.delete_skill(folder)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}
