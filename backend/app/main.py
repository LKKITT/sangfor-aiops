"""后端入口：FastAPI 应用 + 定时任务（每日自动备份/更新信息刷新）+ 默认设备初始化。"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.adapters.factory import close_all_clients, start_keepalive
from app.adapters.simulator.app import create_simulator_app
from app.adapters.simulator.state import STATE
from app.api import backups, chat, devices, updates
from app.api import knowledge, settings as settings_api
from app.config import settings
from app.services import config_service, update_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sangfor-agent")


async def scheduled_backup_all() -> None:
    for device in db.list_devices():
        try:
            rec = await config_service.create_backup(device["id"],
                                                     label=f"每日自动备份", kind="scheduled",
                                                     created_by="scheduler")
            log.info("定时备份完成 device=%s backup=%s", device["id"], rec["id"])
        except Exception as e:   # noqa: BLE001
            log.warning("定时备份失败 device=%s: %s", device["id"], e)


async def scheduled_update_refresh() -> None:
    try:
        result = await update_service.refresh_update_cache("af")
        log.info("更新信息刷新完成: %s", result)
    except Exception as e:   # noqa: BLE001
        log.warning("更新信息刷新失败: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _ensure_default_device()
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(scheduled_backup_all, "cron", hour=settings.auto_backup_hour, minute=0,
                      id="daily_backup")
    scheduler.add_job(scheduled_update_refresh, "cron", hour=settings.update_refresh_hour,
                      minute=30, id="update_refresh")
    scheduler.start()
    start_keepalive()
    log.info("定时任务已启动：每日 %02d:00 自动备份；每日 %02d:30 刷新更新信息；每 3 分钟设备会话保活",
             settings.auto_backup_hour, settings.update_refresh_hour)
    yield
    scheduler.shutdown(wait=False)
    await close_all_clients()


def _ensure_default_device() -> None:
    if not db.list_devices():
        device = db.upsert_device({
            "id": db.new_id("dev_"), "name": "演示-AF模拟器", "type": "af", "mode": "simulator",
            "base_url": "", "username": settings.simulator_auth["username"],
            "password": settings.simulator_auth["password"],
            "readonly": 0, "settings_json": "{}", "created_at": db.now(),
        })
        log.info("已初始化默认模拟器设备：%s（%s）", device["name"], device["id"])


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(devices.router)
app.include_router(backups.router)
app.include_router(chat.router)
app.include_router(updates.router)
app.include_router(knowledge.router)
app.include_router(settings_api.router)
# 模拟器挂载到 /simulator 便于独立调试观察（演示时可展示设备端视角）
app.mount("/simulator", create_simulator_app(STATE), name="simulator")


@app.get("/api/health")
def health() -> dict:
    from app.services.app_settings import get_llm_config
    llm = get_llm_config()
    return {"ok": True, "app": settings.app_name,
            "llm_configured": bool(llm["api_key"]) and not llm["api_key"].startswith("your-"),
            "readonly_mode": settings.readonly_mode}
