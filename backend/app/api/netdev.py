"""网络设备管理 API：SSH 交换机/路由器的设备 CRUD、连接测试、批量并行命令执行、
WebSocket 交互式控制台（浏览器 xterm.js 直连设备 CLI）。"""
import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app import db
from app.services import netdev_service

log = logging.getLogger("sangfor-agent.netdev")

router = APIRouter(prefix="/api/netdev", tags=["netdev"])

VENDORS = ("huawei", "h3c", "cisco", "ruijie", "zte", "juniper",
           "aruba", "dell", "tplink", "mikrotik", "nokia", "other")


def _public(d: dict) -> dict:
    """出参脱敏：不回传密码，仅标记是否已配置。"""
    return {**{k: v for k, v in d.items() if k not in ("password", "enable_password")},
            "has_password": bool(d.get("password")),
            "has_enable_password": bool(d.get("enable_password"))}


class NetDevIn(BaseModel):
    name: str
    host: str
    vendor: str = "huawei"
    model: str = ""
    port: int = 22
    username: str = ""
    password: str = ""
    enable_password: str = ""
    group_name: str = ""
    device_id: str = ""   # 编辑已有设备时传入：按 id 更新（口令留空=保留原值）


class BatchIn(BaseModel):
    devices: list[NetDevIn]


class TestIn(BaseModel):
    device_id: str
    timeout: float = 15


class ExecuteIn(BaseModel):
    device_ids: list[str]
    commands: list[str]
    name: str = ""
    timeout: float = 30


@router.get("/devices")
def list_devices(group: str = "") -> list[dict]:
    return [_public(d) for d in db.list_netdev_devices(group=group)]


@router.get("/vendors")
def vendors() -> list[dict]:
    return [{"vendor": v.vendor, "display": v.display, "version_cmd": v.version_cmd,
             "paging_cmd": v.paging_cmd}
            for v in netdev_service.VENDOR_PROFILES.values()]


@router.post("/devices")
def add_device(payload: NetDevIn) -> dict:
    if payload.vendor not in VENDORS:
        raise HTTPException(400, f"不支持的厂家类型：{payload.vendor}")
    if not payload.host.strip():
        raise HTTPException(400, "管理地址不能为空")
    data = payload.model_dump()
    if data.get("device_id"):
        existing = db.get_netdev_device(data["device_id"])
        if not existing:
            raise HTTPException(404, "要编辑的设备不存在")
        # 口令留空 = 保留原值（编辑弹窗出于脱敏不回传口令）
        if not data.get("password"):
            data["password"] = existing["password"]
        if not data.get("enable_password"):
            data["enable_password"] = existing["enable_password"]
        data["id"] = data.pop("device_id")
    else:
        data.pop("device_id", None)
    try:
        rec = db.save_netdev_device(data)
    except Exception as e:   # noqa: BLE001 —— 同 host:port 冲突等
        raise HTTPException(400, f"保存失败：{e}") from e
    return _public(rec)


@router.post("/devices/batch")
def add_devices_batch(payload: BatchIn) -> dict:
    """批量添加：逐行校验保存，单行失败不阻塞其余，返回成功/失败明细。"""
    saved, failed = [], []
    for item in payload.devices:
        if item.vendor not in VENDORS:
            failed.append({"host": item.host, "name": item.name, "reason": f"不支持的厂家：{item.vendor}"})
            continue
        if not item.host.strip():
            failed.append({"host": item.host, "name": item.name, "reason": "管理地址为空"})
            continue
        try:
            saved.append(_public(db.save_netdev_device(item.model_dump())))
        except Exception as e:   # noqa: BLE001
            failed.append({"host": item.host, "name": item.name, "reason": str(e)})
    return {"ok": True, "saved": len(saved), "failed": failed, "devices": saved}


@router.get("/devices/export")
def export_devices() -> dict:
    """导出设备清单为 CSV（含口令列，供备份/迁移后经批量导入恢复）。"""
    import csv as _csv
    import io
    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(["名称", "厂家", "管理地址", "端口", "用户名", "SSH口令",
                     "提权口令", "分组", "型号"])
    for d in db.list_netdev_devices():
        writer.writerow([d["name"], d["vendor"], d["host"], d["port"], d["username"],
                         d["password"], d["enable_password"], d["group_name"], d["model"]])
    return {"ok": True, "filename": "网络设备导出.csv", "csv": buf.getvalue()}


@router.delete("/devices/{device_id}")
def remove_device(device_id: str) -> dict:
    if not db.get_netdev_device(device_id):
        raise HTTPException(404, "设备不存在")
    db.delete_netdev_device(device_id)
    return {"ok": True}


@router.post("/devices/test")
async def test_device(payload: TestIn) -> dict:
    """连接测试：建立 SSH 会话并执行版本查看命令，返回连通性与关键输出。"""
    device = db.get_netdev_device(payload.device_id)
    if not device:
        raise HTTPException(404, "设备不存在")
    prof = netdev_service.profile_of(device["vendor"])
    result = await netdev_service.run_commands(
        device, [prof.version_cmd], timeout=payload.timeout)
    if not result["ok"]:
        return {"ok": False, "error": result.get("error", "连接失败")}
    db.update_netdev_last_ok(device_id=payload.device_id)
    output = result.get("output", "")
    # 版本命令输出通常很长，截取头部关键信息
    head = "\n".join(output.splitlines()[:14])
    return {"ok": True, "vendor": device["vendor"], "duration": result.get("duration", 0),
            "output": head}


@router.post("/execute")
async def execute(payload: ExecuteIn) -> dict:
    """批量并行执行：选中设备 × 命令列表，返回 task_id 供进度轮询。"""
    if not payload.commands or not [c for c in payload.commands if c.strip()]:
        raise HTTPException(400, "命令列表不能为空")
    devices = []
    for did in payload.device_ids:
        d = db.get_netdev_device(did)
        if not d:
            raise HTTPException(404, f"设备不存在：{did}")
        devices.append(d)
    if not devices:
        raise HTTPException(400, "请至少选择一台设备")
    task_id = netdev_service.start_batch_task(
        payload.name, devices, [c.strip() for c in payload.commands if c.strip()],
        timeout=min(max(payload.timeout, 5), 300))
    return {"ok": True, "task_id": task_id, "total": len(devices)}


@router.get("/tasks")
def tasks(limit: int = 20) -> list[dict]:
    return db.list_netdev_tasks(limit=min(limit, 100))


@router.get("/tasks/{task_id}")
def task_detail(task_id: str) -> dict:
    task = db.get_netdev_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.websocket("/ws/{device_id}")
async def ws_terminal(ws: WebSocket, device_id: str) -> None:
    """交互式控制台：浏览器 xterm.js ⇄ WebSocket ⇄ 设备 SSH 会话（双向透传）。

    消息协议（JSON）：in: {type:data,text} / {type:resize,cols,rows}；
    out: {type:data,text} / {type:error,text} / {type:closed}。
    """
    await ws.accept()
    device = db.get_netdev_device(device_id)
    if not device:
        await ws.send_text(json.dumps({"type": "error", "text": "设备不存在"}))
        await ws.close()
        return
    if not netdev_service.SSH_AVAILABLE:
        await ws.send_text(json.dumps({"type": "error", "text": "asyncssh 未安装"}))
        await ws.close()
        return
    try:
        conn = await asyncio.wait_for(asyncssh_connect(device), timeout=12)
        proc = await conn.create_process(term_type="xterm-256color",
                                         term_size=(120, 32))
    except Exception as e:   # noqa: BLE001 —— 认证/网络/参数异常统一友好下发
        log.warning("控制台连接失败 device=%s: %s", device_id, e)
        await ws.send_text(json.dumps({"type": "error", "text": f"SSH 连接失败：{e}"}))
        await ws.close()
        return

    async def pump_out() -> None:
        try:
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                await ws.send_text(json.dumps({"type": "data", "text": data}))
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:   # noqa: BLE001
            log.debug("控制台输出泵退出 device=%s", device_id, exc_info=True)

    async def pump_in() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if msg.get("type") == "data":
                proc.stdin.write(str(msg.get("text", "")))
            elif msg.get("type") == "resize":
                try:
                    proc.change_terminal_size(int(msg.get("cols") or 120),
                                              int(msg.get("rows") or 32))
                except Exception:   # noqa: BLE001 —— 尺寸变更失败不影响会话
                    pass
            elif msg.get("type") == "close":
                return

    try:
        await asyncio.gather(pump_out(), pump_in())
    except WebSocketDisconnect:
        pass
    finally:
        try:
            conn.close()
        except Exception:   # noqa: BLE001
            pass
        try:
            await ws.close()
        except Exception:   # noqa: BLE001
            pass


def asyncssh_connect(device: dict):
    """独立封装便于测试替换；参数与批量执行保持一致（含老设备传统算法扩展）。"""
    import asyncssh
    return asyncssh.connect(**netdev_service.ssh_connect_kwargs(device))


# ---------------- 网络拓扑（LLDP/ARP 自动发现 + 分组拓扑 + 资产定位） ----------------

from app.services import netdev_topology_service as topo  # noqa: E402


class TopologyPositionsIn(BaseModel):
    group: str = ""
    positions: dict = {}          # {device_id: [x, y]}


@router.get("/topology")
async def get_topology(group: str = "", force: int = 0) -> dict:
    """分组拓扑：LLDP 链路（优先）+ ARP 资产索引。force=1 强制重新采集（TTL 外自动增量采集）。"""
    if not netdev_service.SSH_AVAILABLE and force:
        raise HTTPException(400, "asyncssh 未安装，无法采集拓扑")
    devices = db.list_netdev_devices(group)
    groups = sorted({d.get("group_name", "") for d in db.list_netdev_devices()})
    if not devices and not group:
        return {"group": "", "groups": groups, "nodes": [], "edges": [],
                "arp_index": [], "positions": {}, "stats": {}, "collect_stats": {}}
    return await topo.topology_payload(group, force=bool(force))


@router.get("/topology/search")
async def search_topology(group: str = "", q: str = "") -> dict:
    """资产定位：IP / MAC / 设备名 → 所在设备与端口。"""
    return topo.search_asset(group, q)


@router.post("/topology/positions")
async def save_topology_positions(payload: TopologyPositionsIn) -> dict:
    """保存手动布局坐标（整组覆盖）。"""
    db.save_netdev_topology_positions(payload.group, payload.positions)
    return {"ok": True}


@router.delete("/topology/positions")
async def clear_topology_positions(group: str = "") -> dict:
    db.save_netdev_topology_positions(group, {})
    return {"ok": True}
