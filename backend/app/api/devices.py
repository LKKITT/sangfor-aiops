"""设备与配置可视化 API。"""
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import db
from app.adapters.base import DeviceError
from app.adapters.factory import get_client
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
    type: str | None = None
    mode: str | None = None
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


@router.post("/test-connection")
async def test_connection(payload: DeviceIn) -> dict:
    """在添加设备前测试连接（设备尚未入库）。"""
    if payload.mode == "simulator":
        return {"ok": True, "message": "模拟器设备，无需测试连接"}
    from app.adapters.factory import create_client
    tmp_device = {
        "id": "_test_", "name": payload.name, "type": payload.type, "mode": payload.mode,
        "base_url": payload.base_url, "username": payload.username, "password": payload.password,
        "readonly": 0, "settings_json": "{}", "created_at": db.now(),
    }
    try:
        client = create_client(tmp_device)
        await client.login()
        sw_version = "unknown"
        model = ""
        try:
            status = await client.get_status()
            sw_version = status.sw_version
            model = status.model
        except Exception as e:
            # get_status 失败不影响连接测试结果，但记录原因
            pass
        await client.aclose()
        # AC 设备：必须以获取到软件版本为成功标准
        if payload.type == "ac":
            if not sw_version or sw_version == "unknown" or sw_version == "AC（开放接口）":
                return {"ok": False, "error": "无法获取 AC 设备版本信息，请检查开放接口共享密钥和来源 IP 白名单配置"}
            msg = f"连接成功，版本 {sw_version}"
            return {"ok": True, "sw_version": sw_version, "model": model, "message": msg}
        msg = f"连接成功"
        if model:
            msg += f"：{model}"
        if sw_version and sw_version != "unknown":
            msg += f"，版本 {sw_version}"
        else:
            msg += "（版本信息获取失败，设备 systemversion 端点不可用，不影响正常使用）"
        return {"ok": True, "sw_version": sw_version, "model": model, "message": msg}
    except Exception as e:
        err_msg = str(e)
        # 对常见错误给出更友好的提示
        if "ConnectError" in err_msg or "Connection refused" in err_msg:
            err_msg = f"无法连接到设备 {payload.base_url}，请检查设备地址是否正确、设备是否在线"
        elif "Login failed" in err_msg or "未获取到 token" in err_msg:
            err_msg = "登录失败，请检查 API 账号和密码是否正确"
        elif "token" in err_msg.lower() and "login" in err_msg.lower():
            err_msg = "登录失败，请检查 API 账号和密码是否正确"
        return {"ok": False, "error": err_msg}


@router.post("/{device_id}/test")
async def test_device(device_id: str) -> dict:
    device = _require(device_id)
    try:
        client = await get_client(device_id)   # 共享客户端（复用登录会话）
        sw_version = "unknown"
        model = ""
        try:
            status = await client.get_status()
            sw_version = status.sw_version
            model = status.model
        except Exception:
            pass   # 获取状态失败不影响测试结果
        return {"ok": True, "sw_version": sw_version, "model": model}
    except Exception as e:   # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ---------------- 实时配置查询（可视化面板数据源） ----------------

async def _with_client(device_id: str, fn) -> Any:
    _require(device_id)
    try:
        client = await get_client(device_id)   # 共享客户端，复用登录会话
        return await fn(client)
    except DeviceError as e:
        raise HTTPException(502, f"设备连接失败：{e}")


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


@router.get("/{device_id}/zones")
async def get_zones(device_id: str) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_zones())


@router.get("/{device_id}/nat")
async def get_nat(device_id: str) -> list[dict]:
    rules = await _with_client(device_id, lambda c: c.get_nat_rules())
    return [r.to_dict() for r in rules]


@router.get("/{device_id}/acl")
async def get_acl(device_id: str) -> list[dict]:
    rules = await _with_client(device_id, lambda c: c.get_acl_rules())
    return [r.to_dict() for r in rules]


@router.get("/{device_id}/bindings")
async def get_bindings(device_id: str, keyword: str = "") -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_user_bindings(keyword))
    kw = keyword.strip().lower()
    if kw:   # 非开放接口设备本地过滤
        rows = [r for r in rows if kw in json.dumps(r.to_dict(), ensure_ascii=False).lower()]
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/ipmac_bindings")
async def get_ipmac_bindings(device_id: str, keyword: str = "") -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_ipmac_bindings(keyword))
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/objects")
async def get_objects(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_network_objects())
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/services")
async def get_services(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_services())
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/routes")
async def get_routes(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_static_routes())
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/snapshot")
async def get_snapshot(device_id: str) -> dict:
    return await _with_client(device_id, lambda c: c.snapshot_config())


# ---------------- AC 特有端点（仅 AC 设备有效） ----------------

@router.get("/{device_id}/ac/online-users")
async def get_ac_online_users(device_id: str) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_online_users())


@router.get("/{device_id}/ac/net-policies")
async def get_ac_net_policies(device_id: str) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_net_policies())


@router.get("/{device_id}/ac/flux-policies")
async def get_ac_flux_policies(device_id: str) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_flux_policies())


@router.get("/{device_id}/ac/throughput")
async def get_ac_throughput(device_id: str) -> dict:
    return await _with_client(device_id, lambda c: c.get_throughput())


@router.get("/{device_id}/ac/app-rank")
async def get_ac_app_rank(device_id: str, top: int = 10) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_app_rank(top))


@router.get("/{device_id}/ac/user-rank")
async def get_ac_user_rank(device_id: str, top: int = 10) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_user_rank(top))


# ---------------- 配置体检 ----------------

@router.post("/{device_id}/checkup")
async def run_checkup(device_id: str) -> dict:
    device = _require(device_id)

    async def do(client) -> dict:
        snapshot = await client.snapshot_config()
        status = (await client.get_status()).to_dict()
        return run_checks(snapshot, status, device.get("type", ""))

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
