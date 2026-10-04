"""配置备份 / 快照 diff / 恢复服务。

备份分两层（基于官方文档调研结论）：
1. 结构化配置快照 —— 通过设备 REST API 拉取关键配置（接口/路由/NAT/ACL/绑定），
   可解析、可视化、可 diff、可恢复；
2. 配置文件归档 —— .conf/.bcf 私有格式文件（真实设备需 Web 控制台，尽力而为），
   只做存储与完整性校验，不解析内容。

恢复流程（两阶段）：preview 生成结构化变更计划 → 确认 → apply 按序回放
（先删后改再建；失败即停；执行前自动做一次 pre_change 安全备份）。
"""
import hashlib
import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

from app.adapters.base import ChangeOp, DeviceClient, DeviceError
from app.adapters.factory import get_client
from app import db
from app.services.device_cache import device_cache

# diff 时忽略的易变字段（运行时统计，非配置）
VOLATILE_FIELDS = {"hit_count", "rx_kbps", "tx_kbps"}
# 参与备份/恢复的配置节（key → 中文名）
SECTIONS = {
    "objects": "网络对象",
    "services": "自定义服务",
    "user_bindings": "用户绑定",
    "acl_rules": "访问控制策略",
    "nat_rules": "NAT 策略",
    "static_routes": "静态路由",
    "interfaces": "网络接口",
}
# 快照节名 → 适配器资源名（恢复回放时使用）
RESOURCE_OF_SECTION = {"objects": "object", "services": "service",
                       "user_bindings": "binding", "acl_rules": "acl",
                       "nat_rules": "nat", "static_routes": "route"}
_SECTION_OF_RESOURCE = {v: k for k, v in RESOURCE_OF_SECTION.items()}
# 支持恢复回放的配置节（接口仅可视化对比，不做恢复）
RESTORE_SECTIONS = ["objects", "services", "user_bindings", "acl_rules",
                    "nat_rules", "static_routes"]
# 依赖安全的回放顺序：先建对象/服务（被引用方），再改规则，最后删规则、删对象/服务
_PLAN_ORDER = [
    ("create", "objects"), ("create", "services"),
    ("update", "objects"), ("update", "services"),
    ("create", "user_bindings"), ("create", "acl_rules"), ("create", "nat_rules"),
    ("create", "static_routes"),
    ("update", "user_bindings"), ("update", "acl_rules"), ("update", "nat_rules"),
    ("update", "static_routes"),
    ("delete", "user_bindings"), ("delete", "acl_rules"), ("delete", "nat_rules"),
    ("delete", "static_routes"),
    ("delete", "objects"), ("delete", "services"),
]


@dataclass
class FieldChange:
    field_name: str
    old: object
    new: object


@dataclass
class RestorePlan:
    device_id: str
    backup_id: str
    ops: list[ChangeOp] = field(default_factory=list)

    def summary(self) -> dict:
        by_op = {"delete": [], "update": [], "create": []}
        for op in self.ops:
            name = op.data.get("name") or op.current.get("name") or op.target_id
            section = _SECTION_OF_RESOURCE.get(op.resource, op.resource)
            by_op[op.op].append({
                "resource": op.resource, "resource_cn": SECTIONS.get(section, section),
                "target_id": op.target_id, "name": name,
                "data": op.data, "current": op.current,
            })
        return {
            "device_id": self.device_id, "backup_id": self.backup_id,
            "total": len(self.ops),
            "delete": by_op["delete"], "update": by_op["update"], "create": by_op["create"],
        }


# ---------------- 备份 ----------------

async def create_backup(device_id: str, label: str, kind: str = "manual",
                        created_by: str = "user") -> dict:
    client: DeviceClient = await get_client(device_id)   # 共享客户端
    snapshot = await client.snapshot_config()
    file_bytes, filename = b"", ""
    try:
        file_bytes, filename = await client.backup_config_file()
    except (DeviceError, NotImplementedError) as e:
        db.audit("backup.file_skipped", {"reason": str(e)}, device_id=device_id, result="degraded")

    backup_id = db.new_id("bk_")
    file_path, file_sha = "", ""
    if file_bytes:
        path = db.backup_file_path(backup_id, ".conf" if filename.endswith(".conf") else ".bcf")
        path.write_bytes(file_bytes)
        file_path = str(path)
        file_sha = hashlib.sha256(file_bytes).hexdigest()
    rec = {
        "id": backup_id, "device_id": device_id,
        "label": label or f"备份-{datetime.now():%Y%m%d %H:%M:%S}",
        "kind": kind, "sw_version": snapshot["meta"].get("sw_version", ""),
        "snapshot_json": json.dumps(snapshot, ensure_ascii=False),
        "file_path": file_path, "file_sha256": file_sha,
        "created_at": db.now(), "created_by": created_by,
    }
    backup = db.create_backup(rec)
    db.audit("backup.create", {"backup_id": backup_id, "label": label, "kind": kind,
                               "has_file": bool(file_bytes)},
             device_id=device_id, actor=created_by)
    return backup


# ---------------- diff ----------------

def _norm_rule(rule: dict) -> dict:
    return {k: v for k, v in rule.items() if k != "id"}


def diff_snapshots(base: dict, target: dict) -> dict:
    """对比两份快照，输出按配置节分组的结构化差异。

    base=旧（目标态来源），target=新（当前设备态）。
    """
    result = {"sections": {}, "summary": {"added": 0, "removed": 0, "changed": 0}}
    for section in SECTIONS:
        base_list = {r.get("id"): r for r in (base.get(section) or [])}
        target_list = {r.get("id"): r for r in (target.get(section) or [])}
        sec = {"added": [], "removed": [], "changed": []}
        for rid, rule in base_list.items():
            if rid not in target_list:
                sec["removed"].append({"id": rid, "name": rule.get("name", rid), "snapshot": rule})
                continue
            changed = []
            cur = _norm_rule(rule)
            new = _norm_rule(target_list[rid])
            for k in sorted(set(cur) | set(new)):
                if k in VOLATILE_FIELDS:
                    continue
                if cur.get(k) != new.get(k):
                    changed.append({"field": k, "old": cur.get(k), "new": new.get(k)})
            if changed:
                sec["changed"].append({"id": rid, "name": rule.get("name", rid), "fields": changed})
        for rid, rule in target_list.items():
            if rid not in base_list:
                sec["added"].append({"id": rid, "name": rule.get("name", rid), "snapshot": rule})
        result["sections"][section] = sec
        result["summary"]["added"] += len(sec["added"])
        result["summary"]["removed"] += len(sec["removed"])
        result["summary"]["changed"] += len(sec["changed"])
    # meta 差异单独展示
    meta_base, meta_target = base.get("meta", {}), target.get("meta", {})
    result["meta"] = {
        "base": {k: meta_base.get(k) for k in ("device_name", "sw_version")},
        "target": {k: meta_target.get(k) for k in ("device_name", "sw_version")},
    }
    return result


def diff_backups(backup_a_id: str, backup_b_id: str) -> dict:
    a, b = db.get_backup(backup_a_id), db.get_backup(backup_b_id)
    if not a or not b:
        raise ValueError("备份记录不存在")
    return {
        "base": {"id": a["id"], "label": a["label"], "created_at": a["created_at"]},
        "target": {"id": b["id"], "label": b["label"], "created_at": b["created_at"]},
        "diff": diff_snapshots(json.loads(a["snapshot_json"]), json.loads(b["snapshot_json"])),
    }


def diff_backup_with_device(backup_id: str, device_snapshot: dict) -> dict:
    b = db.get_backup(backup_id)
    if not b:
        raise ValueError("备份记录不存在")
    return diff_snapshots(json.loads(b["snapshot_json"]), device_snapshot)


# ---------------- 恢复 ----------------

def build_restore_plan(device_snapshot: dict, backup_snapshot: dict) -> RestorePlan:
    """由「备份快照(目标态)」与「当前设备快照」生成回放计划。

    以备份为目标态：当前设备多出的删除（delete）、不一致的更新（update）、
    备份有而设备没有的重建（create）。按依赖安全顺序编排（_PLAN_ORDER）：
    先创建/更新网络对象与自定义服务（被引用方），再处理规则，
    最后删规则、删对象/服务，避免恢复过程中出现引用悬空。
    """
    plan = RestorePlan(device_id=backup_snapshot.get("meta", {}).get("device_id", ""),
                       backup_id="")
    op_index: dict[tuple, ChangeOp] = {}
    for section in RESTORE_SECTIONS:
        backup_list = {r.get("id"): r for r in (backup_snapshot.get(section) or [])}
        device_list = {r.get("id"): r for r in (device_snapshot.get(section) or [])}
        resource = RESOURCE_OF_SECTION[section]
        for rid, cur in device_list.items():          # 设备有、备份没有 → 删除
            if rid not in backup_list:
                op_index[(section, "delete", rid)] = ChangeOp(
                    op="delete", resource=resource, target_id=rid, current=cur)
        for rid, goal in backup_list.items():          # 两边都有但内容不一致 → 更新
            if rid not in device_list:
                continue
            cur = _norm_rule(device_list[rid])
            tgt = _norm_rule(goal)
            if any(cur.get(k) != tgt.get(k) for k in set(cur) | set(tgt) if k not in VOLATILE_FIELDS):
                op_index[(section, "update", rid)] = ChangeOp(
                    op="update", resource=resource, target_id=rid,
                    data=goal, current=device_list[rid])
        for rid, goal in backup_list.items():          # 备份有、设备没有 → 创建
            if rid not in device_list:
                data = dict(goal)
                data["id"] = rid
                op_index[(section, "create", rid)] = ChangeOp(
                    op="create", resource=resource, target_id=rid, data=data)
    # 按依赖安全顺序展开
    for op_kind, section in _PLAN_ORDER:
        for (sec, kind, rid), op in list(op_index.items()):
            if sec == section and kind == op_kind:
                plan.ops.append(op)
                op_index.pop((sec, kind, rid))
    plan.ops.extend(op_index.values())   # 兜底：未匹配到的操作仍按序追加
    return plan


async def restore_preview(device_id: str, backup_id: str) -> dict:
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise ValueError("备份记录不存在或不属于该设备")
    client = await get_client(device_id)
    device_snapshot = await client.snapshot_config()
    plan = build_restore_plan(device_snapshot, json.loads(backup["snapshot_json"]))
    plan.backup_id = backup_id
    s = plan.summary()
    s["label"] = backup["label"]
    s["backup_sw_version"] = backup["sw_version"]
    return s


async def restore_apply(device_id: str, backup_id: str, operator: str = "user") -> dict:
    """执行恢复：先做 pre_change 安全备份，再按序回放；任一步失败即停并报告进度。"""
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise ValueError("备份记录不存在或不属于该设备")
    safety = await create_backup(device_id, label=f"恢复前安全备份（目标 {backup['label']}）",
                                 kind="pre_change", created_by="system")
    client = await get_client(device_id)
    executed, errors = [], []
    try:
        device_snapshot = await client.snapshot_config()
        plan = build_restore_plan(device_snapshot, json.loads(backup["snapshot_json"]))
        plan.backup_id = backup_id
        for op in plan.ops:
            try:
                result = await client.apply_change(op)
                executed.append({"op": op.op, "resource": op.resource, "target_id": op.target_id,
                                 "message": result["message"]})
                db.audit("restore.op", {"op": op.op, "resource": op.resource, "target_id": op.target_id},
                         device_id=device_id, actor=operator)
            except DeviceError as e:
                errors.append({"op": op.op, "resource": op.resource, "target_id": op.target_id,
                               "error": str(e)})
                db.audit("restore.op_failed", {"op": op.op, "resource": op.resource,
                                               "target_id": op.target_id, "error": str(e)},
                         device_id=device_id, actor=operator, result="failed")
                break   # 失败即停，保留现场，可用 safety 备份回退
    except DeviceError as e:
        errors.append({"op": "snapshot", "resource": "-", "target_id": "-", "error": str(e)})
    device_cache.invalidate(device_id)   # 配置已回写设备，可视化缓存全失效
    db.audit("restore.apply", {"backup_id": backup_id, "executed": len(executed),
                               "failed": len(errors), "safety_backup": safety["id"]},
             device_id=device_id, actor=operator, result="ok" if not errors else "partial")
    return {"ok": not errors, "safety_backup_id": safety["id"], "executed": executed, "errors": errors}


def cleanup_scheduled_backups(keep: int = 30) -> int:
    """定时备份保留策略：每台设备仅保留最近 keep 份 scheduled 备份（记录 + 配置文件）。

    manual / pre_change 备份永不自动清理。返回删除的备份数。
    """
    if keep <= 0:
        return 0
    removed = 0
    for device in db.list_devices():
        rows = [b for b in db.list_backups(device["id"]) if b.get("kind") == "scheduled"]
        for b in rows[keep:]:
            fp = b.get("file_path")
            if fp:
                try:
                    Path(fp).unlink(missing_ok=True)
                except OSError:
                    pass   # 文件删除失败不阻塞记录清理（下次清理重试）
            db.delete_backup(b["id"])
            removed += 1
    return removed


async def restore_config_file(device_id: str, backup_id: str, operator: str = "user") -> dict:
    """配置文件级恢复（.conf 上传回设备；模拟器支持，真实设备视控制台端点可用性）。"""
    backup = db.get_backup(backup_id)
    if not backup or not backup.get("file_path"):
        raise ValueError("该备份没有配置文件归档")
    with open(backup["file_path"], "rb") as f:
        data = f.read()
    sha = hashlib.sha256(data).hexdigest()
    if sha != backup["file_sha256"]:
        raise ValueError("配置文件完整性校验失败（SHA256 不匹配），已中止恢复")
    client = await get_client(device_id)
    result = await client.restore_config_file(data)
    db.audit("restore.config_file", {"backup_id": backup_id}, device_id=device_id, actor=operator)
    return {"ok": True, "detail": result}
