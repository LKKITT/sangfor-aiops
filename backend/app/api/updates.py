"""软件更新信息 API。"""
from fastapi import APIRouter, HTTPException

from app.adapters.factory import get_client
from app import db
from app.services import update_service, upgrade_advisor

router = APIRouter(prefix="/api", tags=["updates"])


async def _current_version(device_id: str) -> str:
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "设备不存在")
    client = await get_client(device_id)
    status = await client.get_status()
    return status.sw_version, status.to_dict()


@router.get("/devices/{device_id}/updates")
async def get_updates(device_id: str) -> dict:
    version, _ = await _current_version(device_id)
    return await update_service.get_update_overview(version)


@router.get("/software-list")
async def software_list(product: str = "af", force: bool = False) -> dict:
    """官方软件更新列表（AF: product_id=13 / AC: product_id=22），支持已配置 Cookie 抓取。"""
    if product not in ("af", "ac"):
        raise HTTPException(400, "product 仅支持 af / ac")
    return await update_service.get_software_list(product, force=force)


@router.get("/devices/{device_id}/upgrade-advice")
async def get_upgrade_advice(device_id: str) -> dict:
    device = db.get_device(device_id)
    version, status = await _current_version(device_id)
    advice = await upgrade_advisor.build_upgrade_advice(version, status, device["name"])
    db.audit("upgrade.advice", {"current": advice["current_version"],
                                "risk": advice["risk"]}, device_id=device_id)
    return advice


@router.post("/updates/refresh")
async def refresh_updates() -> dict:
    return await update_service.refresh_update_cache("af")
