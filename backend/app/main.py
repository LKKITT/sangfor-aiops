"""后端入口：FastAPI 应用 + 定时任务（每日自动备份/更新信息刷新）。"""
import asyncio
import logging
import os
import time
import uuid
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
    sem = asyncio.Semaphore(3)   # 设备侧管理会话有限：限流并发而非全量并发

    async def _one(device: dict) -> None:
        async with sem:
            try:
                rec = await config_service.create_backup(device["id"],
                                                         label="每日自动备份", kind="scheduled",
                                                         created_by="scheduler")
                log.info("定时备份完成 device=%s backup=%s", device["id"], rec["id"])
            except Exception as e:   # noqa: BLE001
                log.warning("定时备份失败 device=%s: %s", device["id"], e)

    await asyncio.gather(*(_one(d) for d in db.list_devices()))


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
    from app.services import mcp_service
    mcp_service.shutdown()


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
_STARTED_AT = time.time()


@app.middleware("http")
async def request_context_middleware(request, call_next):
    """请求上下文：request-id 贯穿（响应头回带）+ 访问日志（方法/路径/状态/耗时）。"""
    rid = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
    request.state.request_id = rid
    t0 = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid
    log.info("%s %s -> %s (%.0fms) [%s]", request.method, request.url.path,
             response.status_code, (time.perf_counter() - t0) * 1000, rid)
    return response
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
    """健康检查 + 运行快照：在线状态、资产数量、待确认动作、近 1h 工具耗时分布。"""
    from app.services.app_settings import get_llm_config
    llm = get_llm_config()
    return {"ok": True, "app": settings.app_name,
            "llm_configured": bool(llm["api_key"]) and not llm["api_key"].startswith("your-"),
            "readonly_mode": settings.readonly_mode,
            "runtime": {"uptime_s": int(time.time() - _STARTED_AT),
                        "sangfor_devices": len(db.list_devices()),
                        "netdev_devices": len(db.list_netdev_devices()),
                        "pending_actions": db.count_pending_actions(),
                        "tool_calls_1h": db.stats_tool_latency(minutes=60)}}
