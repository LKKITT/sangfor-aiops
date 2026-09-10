"""系统设置 API：诸葛知识库社区账号（BBS）、LLM 接入等运行配置的可视化管理（保存即生效）。"""
from fastapi import APIRouter
from pydantic import BaseModel

from app import db
from app.config import settings
from app.services import app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsIn(BaseModel):
    zhuge_bbs_username: str | None = None
    zhuge_bbs_password: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None


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
    }


@router.post("")
def save_settings(payload: SettingsIn) -> dict:
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
    db.audit("settings.save", {
        "zhuge_account_updated": payload.zhuge_bbs_username is not None or payload.zhuge_bbs_password is not None,
        "llm_updated": payload.llm_api_key is not None or payload.llm_model is not None
                       or payload.llm_base_url is not None,
    })
    return get_settings()
