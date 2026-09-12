"""软件更新信息 API。"""
from fastapi import APIRouter, HTTPException

from app.adapters.factory import get_client
from app import db
from app.services import update_service, upgrade_advisor

router = APIRouter(prefix="/api", tags=["updates"])


# 设备类型 → 更新产品线（scp 设备版本串无前缀，需显式指定）
DEVICE_PRODUCT = {"af": "af", "ac": "ac", "scp": "scp"}


async def _current_version(device_id: str) -> tuple[str, dict]:
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "设备不存在")
    client = await get_client(device_id)
    status = await client.get_status()
    return status.sw_version, status.to_dict()


def _device_product(device: dict | None) -> str:
    return DEVICE_PRODUCT.get((device or {}).get("type", ""), "")


@router.get("/devices/{device_id}/updates")
async def get_updates(device_id: str) -> dict:
    device = db.get_device(device_id)
    version, _ = await _current_version(device_id)
    return await update_service.get_update_overview(version, product=_device_product(device))


@router.get("/software-list")
async def software_list(product: str = "af", force: bool = False) -> dict:
    """官方软件更新列表（AF/AC/SCP/HCI 各自产品线入口），支持已配置 Cookie 抓取。"""
    if product not in ("af", "ac", "scp", "hci"):
        raise HTTPException(400, "product 仅支持 af / ac / scp / hci")
    return await update_service.get_software_list(product, force=force)


@router.get("/devices/{device_id}/upgrade-advice")
async def get_upgrade_advice(device_id: str) -> dict:
    device = db.get_device(device_id)
    version, status = await _current_version(device_id)
    advice = await upgrade_advisor.build_upgrade_advice(version, status, device["name"],
                                                        product=_device_product(device))
    db.audit("upgrade.advice", {"current": advice["current_version"],
                                "risk": advice["risk"]}, device_id=device_id)
    return advice


@router.post("/updates/refresh")
async def refresh_updates() -> dict:
    """手动刷新：强制重抓官方数据（绕过缓存新鲜期）。"""
    return await update_service.refresh_update_cache("af", force=True)
