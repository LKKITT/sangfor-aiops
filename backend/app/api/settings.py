"""系统设置 API：深信服技术支持平台 Cookie、LLM 接入等运行配置的可视化管理（保存即生效）。"""
from fastapi import APIRouter
from pydantic import BaseModel

from app import db
from app.config import settings
from app.services import app_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])

COOKIE_KEY = "sangfor_support_cookie"


class SettingsIn(BaseModel):
    support_cookie: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None


@router.get("")
def get_settings() -> dict:
    llm = app_settings.get_llm_config()
    return {
        "support_cookie": db.get_setting(COOKIE_KEY, "") or settings.support_cookie,
        "support_cookie_source": "database" if db.get_setting(COOKIE_KEY, "") else (
            "env" if settings.support_cookie else "none"),
        "llm_base_url": llm["base_url"],
        "llm_model": llm["model"],
        "llm_api_key_set": bool(llm["api_key"]) and not llm["api_key"].startswith("your-"),
        "llm_source": llm["source"],
        "readonly_mode": settings.readonly_mode,
        "auto_backup_hour": settings.auto_backup_hour,
    }


@router.post("")
def save_settings(payload: SettingsIn) -> dict:
    if payload.support_cookie is not None:
        db.set_setting(COOKIE_KEY, payload.support_cookie.strip())
    if payload.llm_base_url is not None:
        db.set_setting(app_settings.LLM_URL_KEY, payload.llm_base_url.strip())
    if payload.llm_api_key is not None and not payload.llm_api_key.startswith("***"):
        db.set_setting(app_settings.LLM_KEY_KEY, payload.llm_api_key.strip())
    if payload.llm_model is not None:
        db.set_setting(app_settings.LLM_MODEL_KEY, payload.llm_model.strip())
    db.audit("settings.save", {
        "support_cookie_set": bool(payload.support_cookie),
        "llm_updated": payload.llm_api_key is not None or payload.llm_model is not None
                       or payload.llm_base_url is not None,
    })
    return get_settings()
