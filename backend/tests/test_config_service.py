"""配置备份 / diff / 恢复闭环测试。"""
import pytest

from app import db
from app.adapters.base import ChangeOp
from app.adapters.factory import create_client
from app.services import config_service


@pytest.mark.asyncio
async def test_backup_diff_restore_roundtrip(device_id):
    # 1) 基线备份
    backup = await config_service.create_backup(device_id, "基线", created_by="pytest")
    assert backup["sw_version"]
    assert backup["file_path"], "模拟器应产出配置文件归档"
    assert len(backup["file_sha256"]) == 64

    # 2) 制造差异：修改一条 ACL + 新建一条 NAT + 删除一条绑定
    client = create_client(db.get_device(device_id))
    await client.login()
    await client.apply_change(ChangeOp(op="update", resource="acl", target_id="acl-005",
                                       data={"action": "deny", "log": True}))
    created = await client.apply_change(ChangeOp(
        op="create", resource="nat",
        data={"name": "回归测试SNAT", "type": "SNAT", "src_addr": "10.9.0.0/16",
              "translated_addr": "202.96.1.2"}))
    nat_id = created["data"]["id"]
    await client.apply_change(ChangeOp(op="delete", resource="binding", target_id="ub-004"))
    await client.aclose()

    # 3) diff：备份 vs 当前设备
    client = create_client(db.get_device(device_id))
    await client.login()
    snapshot_now = await client.snapshot_config()
    await client.aclose()
    diff = config_service.diff_backup_with_device(backup["id"], snapshot_now)
    s = diff["summary"]
    assert s["changed"] >= 1        # acl-005 修改
    assert s["added"] >= 1          # 新建 NAT
    assert s["removed"] >= 1        # 删除绑定
    changed_acl = [c for sec in diff["sections"].values() for c in sec["changed"]
                   if c["id"] == "acl-005"]
    assert changed_acl and any(f["field"] == "action" and f["new"] == "deny"
                               for f in changed_acl[0]["fields"])

    # 4) 恢复预览 → 执行
    preview = await config_service.restore_preview(device_id, backup["id"])
    assert preview["total"] >= 3
    result = await config_service.restore_apply(device_id, backup["id"], operator="pytest")
    assert result["ok"], result["errors"]
    assert len(result["executed"]) == preview["total"]
    assert result["safety_backup_id"]   # 恢复前自动安全备份

    # 5) 恢复后快照与基线一致（忽略命中数）
    client = create_client(db.get_device(device_id))
    await client.login()
    restored = await client.snapshot_config()
    await client.aclose()
    diff2 = config_service.diff_backup_with_device(backup["id"], restored)
    assert diff2["summary"] == {"added": 0, "removed": 0, "changed": 0}


@pytest.mark.asyncio
async def test_two_backup_diff(device_id):
    a = await config_service.create_backup(device_id, "A", created_by="pytest")
    client = create_client(db.get_device(device_id))
    await client.login()
    await client.apply_change(ChangeOp(op="update", resource="acl", target_id="acl-001",
                                       data={"log": True}))
    await client.aclose()
    b = await config_service.create_backup(device_id, "B", created_by="pytest")
    result = config_service.diff_backups(a["id"], b["id"])
    changed = [c for sec in result["diff"]["sections"].values() for c in sec["changed"]]
    assert any(c["id"] == "acl-001" and
               any(f["field"] == "log" for f in c["fields"]) for c in changed)
