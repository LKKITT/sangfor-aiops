"""后端入口：FastAPI 应用 + 定时任务（每日自动备份/更新信息刷新）。"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.adapters.factory import close_all_clients, start_keepalive
from app.api import backups, chat, devices, updates
from app.api import channel, knowledge, netdev, settings as settings_api
from app.config import settings
from app.services import config_service, update_service, wecom_bot_service, zhuge_kb_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sangfor-agent")


async def scheduled_backup_all() -> None:
    for device in db.list_devices():
        try:
            rec = await config_service.create_backup(device["id"],
                                                     label="每日自动备份", kind="scheduled",
                                                     created_by="scheduler")
            log.info("定时备份完成 device=%s backup=%s", device["id"], rec["id"])
        except Exception as e:   # noqa: BLE001
            log.warning("定时备份失败 device=%s: %s", device["id"], e)


async def scheduled_cleanup() -> None:
    removed_db = db.cleanup_expired(retention_days=settings.retention_days,
                                    audit_days=settings.retention_audit_days)
    removed_backups = config_service.cleanup_scheduled_backups(keep=settings.backup_keep_scheduled)
    if any(removed_db.values()) or removed_backups:
        log.info("保留策略清理完成：会话数据=%s 定时备份=%s 份", removed_db, removed_backups)


async def scheduled_update_refresh() -> None:
    for prod in ("af", "ac", "scp", "hci"):
        try:
            result = await update_service.refresh_update_cache(prod)
            log.info("更新信息刷新完成 %s: %s", prod, result.get("official", {}).get("status"))
        except Exception as e:   # noqa: BLE001
            log.warning("更新信息刷新失败 %s: %s", prod, e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if not os.getenv("SF_SKIP_DEMO_CLEANUP"):   # 测试环境跳过（测试夹具依赖模拟器路由）
        _remove_demo_devices()
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    scheduler.add_job(scheduled_backup_all, "cron", hour=settings.auto_backup_hour, minute=0,
                      id="daily_backup")
    scheduler.add_job(scheduled_update_refresh, "cron", hour=settings.update_refresh_hour,
                      minute=30, id="update_refresh")
    scheduler.add_job(scheduled_cleanup, "cron", hour=settings.cleanup_hour, minute=45,
                      id="retention_cleanup")
    scheduler.start()
    start_keepalive()
    await wecom_bot_service.start()
    zhuge_kb_service.prewarm()   # 后台预热官方知识库 SSO 会话，首问免登录等待
    log.info("定时任务已启动：每日 %02d:00 自动备份；每日 %02d:30 刷新更新信息；每 3 分钟设备会话保活",
             settings.auto_backup_hour, settings.update_refresh_hour)
    yield
    await wecom_bot_service.stop()
    scheduler.shutdown(wait=False)
    await close_all_clients()


def _remove_demo_devices() -> None:
    """下线演示设备：清理存量模拟器设备记录（演示模式已移除，AI 助手面向真实设备）。"""
    removed = []
    for d in db.list_devices():
        if d.get("mode") == "simulator":
            db.delete_device(d["id"])
            removed.append(d["name"])
    if removed:
        log.info("已下线演示设备（模拟器模式已移除）：%s", "、".join(removed))


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(devices.router)
app.include_router(backups.router)
app.include_router(chat.router)
app.include_router(updates.router)
app.include_router(knowledge.router)
app.include_router(settings_api.router)
app.include_router(channel.router)
app.include_router(netdev.router)


@app.get("/api/health")
def health() -> dict:
    from app.services.app_settings import get_llm_config
    llm = get_llm_config()
    return {"ok": True, "app": settings.app_name,
            "llm_configured": bool(llm["api_key"]) and not llm["api_key"].startswith("your-"),
            "readonly_mode": settings.readonly_mode}
