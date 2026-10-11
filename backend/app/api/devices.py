"""设备与配置可视化 API。"""
import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, field_validator

from app import db
from app.services.device_cache import CONFIG_TTL, STATUS_TTL, device_cache
from app.adapters.base import DeviceError
from app.adapters.factory import get_client
from app.config import settings
from app.services.analyzer import run_checks

router = APIRouter(prefix="/api/devices", tags=["devices"])


class DeviceIn(BaseModel):
    name: str
    type: str = "af"
    mode: str = "real"                 # real（模拟器演示模式已下线）
    base_url: str = ""
    username: str = ""
    password: str = ""
    readonly: bool = False
    group_name: str = ""               # 安全设备分组；留空归默认分组


class DevicePatch(BaseModel):
    name: str | None = None
    type: str | None = None
    mode: str | None = None
    readonly: bool | None = None
    base_url: str | None = None
    username: str | None = None
    password: str | None = None
    group_name: str | None = None      # 安全设备分组；留空归默认分组


class DeviceOut(BaseModel):
    """设备响应契约：无论入参如何，password 永远以掩码输出（脱敏单点化）。"""
    model_config = ConfigDict(extra="ignore")
    id: str
    name: str
    type: str
    mode: str = "real"
    base_url: str = ""
    username: str = ""
    password: str = "***"
    readonly: int = 0
    group_name: str = "默认分组"
    settings_json: str = "{}"
    created_at: str = ""

    @field_validator("password", mode="before")
    @classmethod
    def _mask(cls, v):
        return "***"


def _require(device_id: str) -> dict:
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, f"设备不存在：{device_id}")
    return device


@router.get("", response_model=list[DeviceOut])
def list_devices() -> list[dict]:
    return db.list_devices()


@router.post("")
async def add_device(payload: DeviceIn) -> dict:
    if payload.mode != "real":
        raise HTTPException(400, "内置模拟器（演示设备）已下线，请以真实设备方式接入")
    if not payload.base_url:
        raise HTTPException(400, "请填写 base_url（如 https://192.168.1.1）")
    device = db.upsert_device({
        "id": db.new_id("dev_"), "name": payload.name, "type": payload.type,
        "mode": payload.mode, "base_url": payload.base_url,
        "username": payload.username, "password": payload.password,
        "readonly": int(payload.readonly), "group_name": payload.group_name,
        "settings_json": "{}", "created_at": db.now(),
    })
    db.audit("device.create", {"name": payload.name, "mode": payload.mode})
    return {**device, "password": "***"}


@router.patch("/{device_id}", response_model=DeviceOut)
def patch_device(device_id: str, payload: DevicePatch) -> dict:
    device = _require(device_id)
    fields = payload.model_dump(exclude_none=True)
    if fields.get("mode") not in (None, "real"):
        raise HTTPException(400, "内置模拟器（演示设备）已下线，接入方式仅支持真实设备")
    if "readonly" in fields:
        fields["readonly"] = int(fields["readonly"])
    device.update(fields)
    saved = db.upsert_device(device)
    device_cache.invalidate(device_id)   # 设备连接信息/权限变更，缓存全失效
    return {**saved, "password": "***"}   # 响应经 DeviceOut 二次脱敏


@router.delete("/{device_id}")
def remove_device(device_id: str) -> dict:
    _require(device_id)
    db.delete_device(device_id)
    device_cache.invalidate(device_id)
    from app.adapters.factory import forget_device
    forget_device(device_id)   # 清理连接缓存/锁/负缓存残留
    db.audit("device.delete", {"device_id": device_id})
    return {"ok": True}


@router.post("/test-connection")
async def test_connection(payload: DeviceIn) -> dict:
    """在添加设备前测试连接（设备尚未入库）。"""
    if payload.mode != "real":
        return {"ok": False, "error": "内置模拟器（演示设备）已下线，请以真实设备方式接入"}
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
        except Exception:
            # get_status 失败不影响连接测试结果，但记录原因
            pass
        await client.aclose()
        # AC/SCP 设备：必须以获取到软件版本为成功标准
        if payload.type in ("ac", "scp"):
            label = "SCP 平台" if payload.type == "scp" else "AC 设备"
            if not sw_version or sw_version == "unknown":
                return {"ok": False,
                        "error": (f"无法获取 {label} 版本信息，请检查"
                                  + ("平台地址与 AccessKey/SecretKey" if payload.type == "scp"
                                     else "开放接口共享密钥和来源 IP 白名单配置"))}
            msg = f"连接成功，版本 {sw_version}"
            return {"ok": True, "sw_version": sw_version, "model": model, "message": msg}
        msg = "连接成功"
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
    _require(device_id)
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

async def _with_client(device_id: str, fn, cache_key: str = "", ttl: float = 0.0) -> Any:
    """执行设备调用。带 cache_key 时走进程内 TTL 缓存（single-flight），仅用于只读可视化端点。"""
    _require(device_id)

    async def _run() -> Any:
        try:
            client = await get_client(device_id)   # 共享客户端，复用登录会话
            # 兜底超时：登录超时/负缓存已挡住不可达设备，这里防端点内部拖长
            return await asyncio.wait_for(fn(client), timeout=settings.device_http_timeout + 5)
        except asyncio.TimeoutError as e:
            raise HTTPException(504, "设备响应超时，请检查设备网络后重试") from e
        except DeviceError as e:
            raise HTTPException(502, f"设备连接失败：{e}") from e

    if cache_key:
        return await device_cache.get_or_load(device_id, cache_key, ttl, _run)
    return await _run()


@router.get("/{device_id}/status")
async def get_status(device_id: str) -> dict:
    _require(device_id)

    async def do(client) -> dict:
        return (await client.get_status()).to_dict()

    return await _with_client(device_id, do, cache_key="status", ttl=STATUS_TTL)


@router.get("/{device_id}/interfaces")
async def get_interfaces(device_id: str) -> list[dict]:
    async def do(client) -> list[dict]:
        rows = [i.to_dict() for i in await client.get_interfaces()]
        # AF：合并状态中心的实时接口状态（连接状态 + 收发速率），失败保留配置层状态
        getter = getattr(client, "get_interface_status", None)
        if getter:
            try:
                realtime = await asyncio.wait_for(getter(rows), timeout=15)
                for row in rows:
                    rt = realtime.get(row.get("name") or "")
                    if not rt:
                        continue
                    row["status"] = "up" if rt.get("connect") else "down"
                    row["rx_kbps"] = rt.get("rx_kbps", 0)
                    row["tx_kbps"] = rt.get("tx_kbps", 0)
            except Exception:   # noqa: BLE001 —— 实时状态失败不影响配置展示
                pass
        return rows

    return await _with_client(device_id, do, cache_key="interfaces", ttl=STATUS_TTL)


@router.get("/{device_id}/zones")
async def get_zones(device_id: str) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_zones(), cache_key="zones", ttl=CONFIG_TTL)


@router.get("/{device_id}/nat")
async def get_nat(device_id: str) -> list[dict]:
    rules = await _with_client(device_id, lambda c: c.get_nat_rules(), cache_key="nat", ttl=CONFIG_TTL)
    return [r.to_dict() for r in rules]


@router.get("/{device_id}/acl")
async def get_acl(device_id: str) -> list[dict]:
    rules = await _with_client(device_id, lambda c: c.get_acl_rules(), cache_key="acl", ttl=CONFIG_TTL)
    return [r.to_dict() for r in rules]


@router.get("/{device_id}/bindings")
async def get_bindings(device_id: str, keyword: str = "") -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_user_bindings(keyword),
                              cache_key=f"bindings:{keyword.strip().lower()}", ttl=CONFIG_TTL)
    kw = keyword.strip().lower()
    if kw:   # 非开放接口设备本地过滤
        rows = [r for r in rows if kw in json.dumps(r.to_dict(), ensure_ascii=False).lower()]
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/ipmac_bindings")
async def get_ipmac_bindings(device_id: str, keyword: str = "") -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_ipmac_bindings(keyword),
                              cache_key=f"ipmac:{keyword.strip().lower()}", ttl=CONFIG_TTL)
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/objects")
async def get_objects(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_network_objects(), cache_key="objects", ttl=CONFIG_TTL)
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/services")
async def get_services(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_services(), cache_key="services", ttl=CONFIG_TTL)
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/routes")
async def get_routes(device_id: str) -> list[dict]:
    rows = await _with_client(device_id, lambda c: c.get_static_routes(), cache_key="routes", ttl=CONFIG_TTL)
    return [r.to_dict() for r in rows]


@router.get("/{device_id}/snapshot")
async def get_snapshot(device_id: str) -> dict:
    return await _with_client(device_id, lambda c: c.snapshot_config(), cache_key="snapshot", ttl=CONFIG_TTL)


# ---------------- AC 特有端点（仅 AC 设备有效） ----------------

@router.get("/{device_id}/ac/online-users")
async def get_ac_online_users(device_id: str) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_online_users(), cache_key="ac_users", ttl=STATUS_TTL)


@router.get("/{device_id}/ac/net-policies")
async def get_ac_net_policies(device_id: str) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_net_policies(), cache_key="ac_netp", ttl=CONFIG_TTL)


@router.get("/{device_id}/ac/flux-policies")
async def get_ac_flux_policies(device_id: str) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_flux_policies(), cache_key="ac_flux", ttl=CONFIG_TTL)


@router.get("/{device_id}/ac/throughput")
async def get_ac_throughput(device_id: str) -> dict:
    return await _with_client(device_id, lambda c: c.get_throughput(), cache_key="ac_tput", ttl=STATUS_TTL)


@router.get("/{device_id}/ac/app-rank")
async def get_ac_app_rank(device_id: str, top: int = 10) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_app_rank(top))


@router.get("/{device_id}/ac/user-rank")
async def get_ac_user_rank(device_id: str, top: int = 10) -> list[dict]:
    return await _with_client(device_id, lambda c: c.get_user_rank(top))


# ---------------- SCP 云计算平台（只读查询） ----------------

def _scp_client(device_id: str):
    device = _require(device_id)
    if device.get("type") != "scp":
        raise HTTPException(400, "该设备不是 SCP 云计算平台")
    return device


@router.get("/{device_id}/scp/platform")
async def scp_platform(device_id: str) -> dict:
    _scp_client(device_id)
    return await _with_client(device_id, lambda c: c.get_scp_platform())


@router.get("/{device_id}/scp/clusters")
async def scp_clusters(device_id: str) -> list[dict]:
    _scp_client(device_id)
    return await _with_client(device_id, lambda c: c.get_scp_clusters())


@router.get("/{device_id}/scp/hosts")
async def scp_hosts(device_id: str, cluster_id: str = "", keyword: str = "") -> list[dict]:
    _scp_client(device_id)
    return await _with_client(device_id,
                              lambda c: c.get_scp_hosts(cluster_id=cluster_id, keyword=keyword))


@router.get("/{device_id}/scp/hosts/{host_id}/interfaces")
async def scp_host_interfaces(device_id: str, host_id: str) -> list[dict]:
    _scp_client(device_id)
    return await _with_client(device_id, lambda c: c.get_scp_host_interfaces(host_id))


@router.get("/{device_id}/scp/vms")
async def scp_vms(device_id: str, host_id: str = "", status: str = "",
                  keyword: str = "", limit: int = 200) -> list[dict]:
    _scp_client(device_id)
    return await _with_client(device_id, lambda c: c.get_scp_vms(
        host_id=host_id, status=status, keyword=keyword, limit=min(max(limit, 1), 1000)))


@router.get("/{device_id}/scp/vms/{vm_id}")
async def scp_vm_detail(device_id: str, vm_id: str) -> dict:
    _scp_client(device_id)
    return await _with_client(device_id, lambda c: c.get_scp_vm_detail(vm_id))


@router.get("/{device_id}/scp/storages")
async def scp_storages(device_id: str) -> list[dict]:
    _scp_client(device_id)
    return await _with_client(device_id, lambda c: c.get_scp_storages())


@router.get("/{device_id}/scp/bvswitches")
async def scp_bvswitches(device_id: str) -> list[dict]:
    _scp_client(device_id)
    return await _with_client(device_id, lambda c: c.get_scp_bvswitches())


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


@router.get("/checkups-overview")
def checkups_overview() -> dict:
    """全局模式看板：按设备聚合最近一次体检（评分/等级/风险计数）。

    数据源为 update_cache 的 checkup_report（最近一次体检缓存）；未体检过的设备
    不出现在列表中。跨客户聚合当前租户可见设备。
    """
    items: list[dict] = []
    with db._connect() as conn:
        rows = conn.execute(
            "SELECT c.device_id, c.payload_json, c.updated_at,"
            " COALESCE(d.name, nd.name, '') AS device_name,"
            " CASE WHEN d.id IS NOT NULL THEN 'sangfor' ELSE 'netdev' END AS family"
            " FROM update_cache c"
            " LEFT JOIN devices d ON d.id = c.device_id"
            " LEFT JOIN netdev_devices nd ON nd.id = c.device_id"
            " WHERE c.cache_key = 'checkup_report'").fetchall()
    for r in rows:
        try:
            rep = json.loads(r["payload_json"] or "{}")
        except Exception:   # noqa: BLE001
            continue
        if not isinstance(rep, dict) or "score" not in rep:
            continue
        items.append({"device_id": r["device_id"], "device_name": r["device_name"] or r["device_id"],
                      "family": r["family"], "score": rep.get("score"),
                      "grade": rep.get("grade", ""), "counts": rep.get("counts", {}),
                      "checked_at": r["updated_at"]})
    items.sort(key=lambda x: (x["score"] if isinstance(x["score"], (int, float)) else 999))
    n = len(items)
    return {"devices": n,
            "avg_score": (round(sum(x["score"] for x in items
                                    if isinstance(x["score"], (int, float))) / n, 1) if n else None),
            "high_risk": sum(int((x["counts"] or {}).get("high", 0)) for x in items),
            "items": items}


@router.get("/{device_id}/checkup/last")
def last_checkup(device_id: str) -> dict:
    _require(device_id)
    cached = db.get_update_cache(device_id, "checkup_report")
    return cached["payload"] if cached else {}
