"""备份管理 API：创建/列表/快照/文件下载/diff/恢复（预览+执行）。"""
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app import db
from app.services import config_service

router = APIRouter(prefix="/api", tags=["backups"])


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


@router.get("/devices/{device_id}/backups/{backup_id}/file")
def download_file(device_id: str, backup_id: str) -> FileResponse:
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise HTTPException(404, "备份不存在")
    if not backup["file_path"]:
        raise HTTPException(404, "该备份没有配置文件归档")
    return FileResponse(backup["file_path"], filename=backup["file_path"].split("\\")[-1].split("/")[-1])


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
    if not payload.confirm:
        raise HTTPException(400, "缺少确认标记")
    try:
        return await config_service.restore_apply(device_id, backup_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
