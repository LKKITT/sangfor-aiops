"""运行时可变配置：界面『平台设置』保存的值优先于 .env。"""
from app import db
from app.config import settings

ZHUGE_USER_KEY = "zhuge_bbs_username"
ZHUGE_PASS_KEY = "zhuge_bbs_password"
LLM_URL_KEY = "llm_base_url"
LLM_KEY_KEY = "llm_api_key"
LLM_MODEL_KEY = "llm_model"
WECOM_ENABLED_KEY = "wecom_aibot_enabled"
WECOM_ID_KEY = "wecom_aibot_id"
WECOM_SECRET_KEY = "wecom_aibot_secret"


def get_wecom_config() -> dict:
    """企微智能机器人渠道配置。来源优先级：界面配置(DB) → .env。

    enabled 的 DB 值（true/false）一旦保存即覆盖 .env 开关；bot_id/secret 同理。
    source 取值 database / env / none（供界面展示配置来源）。
    """
    db_enabled = db.get_setting(WECOM_ENABLED_KEY, "")
    if db_enabled:
        enabled = db_enabled.strip().lower() in ("1", "true", "yes", "on")
    else:
        enabled = settings.wecom_aibot_enabled
    bot_id = db.get_setting(WECOM_ID_KEY, "") or settings.wecom_aibot_id
    secret = db.get_setting(WECOM_SECRET_KEY, "") or settings.wecom_aibot_secret
    has_db = bool(db_enabled or db.get_setting(WECOM_ID_KEY, "")
                  or db.get_setting(WECOM_SECRET_KEY, ""))
    has_env = bool(settings.wecom_aibot_enabled or settings.wecom_aibot_id
                   or settings.wecom_aibot_secret)
    return {"enabled": enabled, "bot_id": bot_id, "secret": secret,
            "source": "database" if has_db else ("env" if has_env else "none")}


def get_zhuge_credentials() -> tuple[str, str, str]:
    """诸葛知识库社区（BBS）SSO 登录凭据。

    来源优先级：界面配置(DB) → .env。返回 (username, password, source)，
    source 取值 database / env / none（none 时由 zhuge_kb_service 回退技能内置账号）。
    """
    user = db.get_setting(ZHUGE_USER_KEY, "")
    pwd = db.get_setting(ZHUGE_PASS_KEY, "")
    if user and pwd:
        return user, pwd, "database"
    user = settings.zhuge_bbs_username
    pwd = settings.zhuge_bbs_password
    if user and pwd:
        return user, pwd, "env"
    return "", "", "none"


def get_llm_config() -> dict:
    return {
        "base_url": db.get_setting(LLM_URL_KEY, "") or settings.llm_base_url,
        "api_key": db.get_setting(LLM_KEY_KEY, "") or settings.llm_api_key,
        "model": db.get_setting(LLM_MODEL_KEY, "") or settings.llm_model,
        "temperature": settings.llm_temperature,
        "source": "database" if db.get_setting(LLM_KEY_KEY, "") else (
            "env" if settings.llm_api_key and not settings.llm_api_key.startswith("your-") else "none"),
    }
