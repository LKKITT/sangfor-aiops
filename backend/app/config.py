"""全局配置：从 backend/.env 读取，全部带合理默认值，保证零配置可跑通模拟器。"""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
load_dotenv(BACKEND_DIR / ".env")


def _bool(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).strip().lower() in ("1", "true", "yes", "on")


class Settings:
    app_name: str = "Sangfor Support Agent"

    # 数据目录（SQLite、备份文件归档）
    data_dir: Path = Path(os.getenv("SF_DATA_DIR", str(PROJECT_DIR / "data")))
    db_path: Path = data_dir / "agent.db"
    backup_dir: Path = data_dir / "backups"

    # LLM（OpenAI 兼容协议）
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "glm-4-flash")
    llm_temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))

    # 运行模式
    readonly_mode: bool = _bool("READONLY_MODE")
    auto_backup_hour: int = int(os.getenv("AUTO_BACKUP_HOUR", "2"))
    update_refresh_hour: int = int(os.getenv("UPDATE_REFRESH_HOUR", "3"))

    # 深信服技术支持平台会话（可选，仅 .env 配置，用于抓取需认证正文；界面不再提供输入）
    support_cookie: str = os.getenv("SANGFOR_SUPPORT_COOKIE", "")

    # 深信服社区 BBS 账号（可选，诸葛知识库 SSO 登录；界面『平台设置』可配置，优先级更高）
    zhuge_bbs_username: str = os.getenv("ZHUGE_BBS_USERNAME", "")
    zhuge_bbs_password: str = os.getenv("ZHUGE_BBS_PASSWORD", "")

    # 设备 HTTP
    device_http_timeout: float = float(os.getenv("DEVICE_HTTP_TIMEOUT", "30"))
    # 设备登录超时：不可达设备在登录阶段快速失败（正常登录 0.2-2s），避免拖满 30s
    device_login_timeout: float = float(os.getenv("DEVICE_LOGIN_TIMEOUT", "8"))
    simulator_auth: dict = {
        "username": os.getenv("SIMULATOR_USERNAME", "admin"),
        "password": os.getenv("SIMULATOR_PASSWORD", "Sangfor@123"),
    }

    # 企业微信智能机器人渠道（WebSocket 长连接，可选；默认关闭不影响现有部署）
    wecom_aibot_enabled: bool = _bool("WECOM_AIBOT_ENABLED")
    wecom_aibot_id: str = os.getenv("WECOM_AIBOT_ID", "")
    wecom_aibot_secret: str = os.getenv("WECOM_AIBOT_SECRET", "")
    # 发送方白名单（逗号分隔 userid，空=允许全部企微成员）
    wecom_allowed_users: str = os.getenv("WECOM_ALLOWED_USERS", "")
    # 渠道端只读模式：false 时企微侧开放写操作确认（文本指令 + 确认卡片），与 Web 端一致；
    # 置 true 可整体关闭渠道端写入口（引导回 Web 界面）
    channel_readonly: bool = _bool("CHANNEL_READONLY_MODE", "false")
    # 渠道会话超时（分钟）：超过该时长未对话，下次消息自动开启新会话（0=关闭该行为）
    channel_session_timeout_min: int = int(os.getenv("CHANNEL_SESSION_TIMEOUT_MINUTES", "30"))

    # 诸葛官方知识库（可选）问答链路：总超时/反问宽限/缓冲轮询间隔（秒）。
    # 收敛长尾等待：超时后编排 LLM 仍会基于本地知识库与设备数据降级作答
    kb_ask_timeout: float = float(os.getenv("KB_ASK_TIMEOUT", "45"))
    kb_clarify_grace: float = float(os.getenv("KB_CLARIFY_GRACE", "3"))
    kb_poll_interval: float = float(os.getenv("KB_POLL_INTERVAL", "0.2"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.backup_dir.mkdir(parents=True, exist_ok=True)
