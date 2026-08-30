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

    # 深信服技术支持平台会话（可选，用于抓取需认证正文）
    support_cookie: str = os.getenv("SANGFOR_SUPPORT_COOKIE", "")

    # 设备 HTTP
    device_http_timeout: float = float(os.getenv("DEVICE_HTTP_TIMEOUT", "10"))
    simulator_auth: dict = {
        "username": os.getenv("SIMULATOR_USERNAME", "admin"),
        "password": os.getenv("SIMULATOR_PASSWORD", "Sangfor@123"),
    }


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.backup_dir.mkdir(parents=True, exist_ok=True)
