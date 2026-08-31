"""运行时可变配置：界面『平台设置』保存的值优先于 .env。"""
from app import db
from app.config import settings

COOKIE_DB_KEY = "sangfor_support_cookie"
LLM_URL_KEY = "llm_base_url"
LLM_KEY_KEY = "llm_api_key"
LLM_MODEL_KEY = "llm_model"


def get_support_cookie() -> str:
    return db.get_setting(COOKIE_DB_KEY, "") or settings.support_cookie


def get_llm_config() -> dict:
    return {
        "base_url": db.get_setting(LLM_URL_KEY, "") or settings.llm_base_url,
        "api_key": db.get_setting(LLM_KEY_KEY, "") or settings.llm_api_key,
        "model": db.get_setting(LLM_MODEL_KEY, "") or settings.llm_model,
        "temperature": settings.llm_temperature,
        "source": "database" if db.get_setting(LLM_KEY_KEY, "") else (
            "env" if settings.llm_api_key and not settings.llm_api_key.startswith("your-") else "none"),
    }
