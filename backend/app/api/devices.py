"""设备与配置可视化 API。"""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.adapters.factory import create_client
from app.services.analyzer import run_checks

router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceIn(BaseModel):
    name: str
    type: str = "af"
    mode: str = "simulator"            # simulator / real
    base_url: str = ""
    username: str = ""
    password: str = ""
    readonly: bool = False


class DevicePatch(BaseModel):
    name: str | None = None
    readonly: bool | None = None
    base_url: str | None = None
    username: str | None = None
    password: str | None = None


def _require(device_id: str) -> dict:
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, f"设备不存在：{device_id}")
    return device


@router.get("")
def list_devices() -> list[dict]:
    return [{**d, "password": "***"} for d in db.list_devices()]


@router.post("")
async def add_device(payload: DeviceIn) -> dict:
    if payload.mode == "real" and not payload.base_url:
        raise HTTPException(400, "真实设备必须填写 base_url（如 https://192.168.1.1）")
    device = db.upsert_device({
        "id": db.new_id("dev_"), "name": payload.name, "type": payload.type,
        "mode": payload.mode, "base_url": payload.base_url,
        "username": payload.username, "password": payload.password,
        "readonly": int(payload.readonly), "settings_json": "{}", "created_at": db.now(),
    })
    db.audit("device.create", {"name": payload.name, "mode": payload.mode})
    return {**device, "password": "***"}


@router.patch("/{device_id}")
def patch_device(device_id: str, payload: DevicePatch) -> dict:
    device = _require(device_id)
    fields = payload.model_dump(exclude_none=True)
    if "readonly" in fields:
        fields["readonly"] = int(fields["readonly"])
    device.update(fields)
    saved = db.upsert_device(device)
    return {**saved, "password": "***"}


@router.delete("/{device_id}")
def remove_device(device_id: str) -> dict:
    _require(device_id)
    db.delete_device(device_id)
    db.audit("device.delete", {"device_id": device_id})
    return {"ok": True}


@router.post("/{device_id}/test")
async def test_device(device_id: str) -> dict:
    device = _require(device_id)
    client = create_client(device)
    try:
        await client.login()
        status = await client.get_status()
        return {"ok": True, "sw_version": status.sw_version, "model": status.model}
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": str(e)}
    finally:
        await client.aclose()


# ---------------- 实时配置查询（可视化面板数据源） ----------------

async def _with_client(device_id: str, fn) -> Any:
    device = _require(device_id)
    client = create_client(device)
    try:
        await client.login()
        return await fn(client)
    finally:
        await client.aclose()


@router.get("/{device_id}/status")
async def get_status(device_id: str) -> dict:
    _require(device_id)

    async def do(client) -> dict:
        return (await client.get_status()).to_dict()

    return await _with_client(device_id, do)


@router.get("/{device_id}/interfaces")
async def get_interfaces(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_interfaces())
    return [i.to_dict() for i in rows]


@router.get("/{device_id}/nat")
async def get_nat(device_id: str) -> list[dict]:
    rules = await _with_client(device_id, lambda c: c.get_nat_rules())
    return [r.to_dict() for r in rules]


@router.get("/{device_id}/acl")
async def get_acl(device_id: str) -> list[dict]:
    rules = await _with_client(device_id, lambda c: c.get_acl_rules())
    return [r.to_dict() for r in rules]


@router.get("/{device_id}/bindings")
async def get_bindings(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_user_bindings())
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/routes")
async def get_routes(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_static_routes())
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/snapshot")
async def get_snapshot(device_id: str) -> dict:
    return await _with_client(device_id, lambda c: c.snapshot_config())


# ---------------- 配置体检 ----------------

@router.post("/{device_id}/checkup")
async def run_checkup(device_id: str) -> dict:
    device = _require(device_id)

    async def do(client) -> dict:
        snapshot = await client.snapshot_config()
        status = (await client.get_status()).to_dict()
        return run_checks(snapshot, status)

    report = await _with_client(device_id, do)
    report["device_name"] = device["name"]
    db.save_update_cache(device_id, "checkup_report", report, "local")
    db.audit("checkup.run", {"score": report["score"], "counts": report["counts"]},
             device_id=device_id)
    return report


@router.get("/{device_id}/checkup/last")
def last_checkup(device_id: str) -> dict:
    _require(device_id)
    cached = db.get_update_cache(device_id, "checkup_report")
    return cached["payload"] if cached else {}
