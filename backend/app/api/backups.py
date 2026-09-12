"""备份管理 API：创建/列表/快照/文件下载/快照导出/diff/恢复（预览+执行）/报告。"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from app import db
from app.config import settings
from app.services import config_service, report_generator
from app.adapters.factory import get_client
from app.services.analyzer import run_checks
from app.services import upgrade_advisor

router = APIRouter(prefix="/api", tags=["backups"])


def _check_writable(device_id: str) -> None:
    """恢复属变更类操作：全局/设备只读模式一律拦截（与对话护栏同源）。"""
    if settings.readonly_mode:
        raise HTTPException(403, "系统处于只读模式（READONLY_MODE=true），恢复操作被禁止")
    device = db.get_device(device_id) or {}
    if device.get("readonly"):
        raise HTTPException(403, f"设备「{device.get('name')}」处于只读模式，恢复操作被禁止")


class BackupIn(BaseModel):
    label: str = ""
    kind: str = "manual"


@router.post("/devices/{device_id}/backups")
async def create_backup(device_id: str, payload: BackupIn) -> dict:
    if not db.get_device(device_id):
        raise HTTPException(404, "设备不存在")
    return await config_service.create_backup(device_id, payload.label, payload.kind)


@router.get("/devices/{device_id}/backups")
def list_backups(device_id: str) -> list[dict]:
    return db.list_backups(device_id)


@router.get("/devices/{device_id}/backups/{backup_id}/snapshot")
def get_snapshot(device_id: str, backup_id: str) -> dict:
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise HTTPException(404, "备份不存在")
    return json.loads(backup["snapshot_json"])


@router.get("/devices/{device_id}/backups/{backup_id}/snapshot/export")
def export_snapshot(device_id: str, backup_id: str) -> JSONResponse:
    """导出结构化配置快照 JSON（含网络对象/服务/路由/策略/绑定），供第三方设备迁移或存档。"""
    from urllib.parse import quote
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise HTTPException(404, "备份不存在")
    snapshot = json.loads(backup["snapshot_json"])
    snapshot.setdefault("meta", {})
    snapshot["meta"]["export_note"] = ("结构化配置快照，可读 JSON 格式；可用于异构设备迁移参照、"
                                       "审计存档。深信服私有格式 .conf 另见配置文件归档。")
    utf8_name = f"{backup['label']}_snapshot.json".replace(" ", "_").replace("/", "_")
    ascii_name = f"snapshot_{backup_id}.json"
    return JSONResponse(content=snapshot, headers={
        "Content-Disposition": (f"attachment; filename=\"{ascii_name}\"; "
                                f"filename*=UTF-8''{quote(utf8_name)}")})


@router.get("/devices/{device_id}/backups/{backup_id}/file")
def download_file(device_id: str, backup_id: str) -> FileResponse:
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise HTTPException(404, "备份不存在")
    if not backup["file_path"]:
        raise HTTPException(404, "该备份没有配置文件归档")
    return FileResponse(backup["file_path"], filename=backup["file_path"].split("\\")[-1].split("/")[-1])


@router.get("/devices/{device_id}/backups/{backup_id}/report")
async def generate_report(device_id: str, backup_id: str) -> Response:
    """生成包含配置可视化、配置体检、软件更新建议的 HTML 报告。"""
    device = db.get_device(device_id)
    if not device:
        raise HTTPException(404, "设备不存在")
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise HTTPException(404, "备份不存在")

    # 1. 从备份快照获取配置可视化数据
    snapshot = json.loads(backup["snapshot_json"])

    # 2. 获取设备实时状态、配置体检、软件更新建议
    status = {}
    checkup = {}
    update_advice = {}
    try:
        client = await get_client(device_id)
        status = (await client.get_status()).to_dict()

        # 配置体检（优先使用缓存；否则用备份快照现场评估，保证报告对应备份时点）
        cached = db.get_update_cache(device_id, "checkup_report")
        checkup = cached["payload"] if cached else {}
        if not checkup:
            checkup = run_checks(snapshot, status, device.get("type", ""))

        # 软件更新建议（SCP 设备版本串无前缀，需显式传产品线）
        update_advice = await upgrade_advisor.build_upgrade_advice(
            status.get("sw_version", ""), status, device["name"],
            product={"af": "af", "ac": "ac", "scp": "scp"}.get(device.get("type", ""), ""))
    except Exception:
        pass

    # 4. 生成 HTML
    html = report_generator.generate_report(
        device_name=device.get("name", ""),
        backup_label=backup.get("label", ""),
        backup_time=backup.get("created_at", ""),
        snapshot=snapshot,
        status=status,
        checkup=checkup,
        update_advice=update_advice,
    )

    # 5. 返回 HTML 文件下载
    from urllib.parse import quote
    safe_name = device.get("name", "device").replace(" ", "_").replace("/", "_")
    filename = f"{safe_name}_report_{backup_id[:8]}.html"
    return Response(content=html, media_type="text/html", headers={
        "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
    })


@router.delete("/devices/{device_id}/backups/{backup_id}")
def delete_backup(device_id: str, backup_id: str) -> dict:
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise HTTPException(404, "备份不存在")
    db.delete_backup(backup_id)
    return {"ok": True}


@router.get("/backups/diff")
def diff_backups(a: str, b: str) -> dict:
    try:
        return config_service.diff_backups(a, b)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class DiffWithDeviceIn(BaseModel):
    snapshot: dict


@router.post("/devices/{device_id}/backups/{backup_id}/diff-device")
def diff_with_device(device_id: str, backup_id: str, payload: DiffWithDeviceIn) -> dict:
    try:
        return config_service.diff_backup_with_device(backup_id, payload.snapshot)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/devices/{device_id}/backups/{backup_id}/restore/preview")
async def restore_preview(device_id: str, backup_id: str) -> dict:
    try:
        return await config_service.restore_preview(device_id, backup_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


class RestoreApplyIn(BaseModel):
    confirm: bool = True


@router.post("/devices/{device_id}/backups/{backup_id}/restore/apply")
async def restore_apply(device_id: str, backup_id: str, payload: RestoreApplyIn) -> dict:
    _check_writable(device_id)
    if not payload.confirm:
        raise HTTPException(400, "缺少确认标记")
    try:
        return await config_service.restore_apply(device_id, backup_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
