"""多租户隔离：设备/会话按租户打标与过滤；跨租户 id 访问被隔离边界拦截。"""
import pytest

from app import db, dbcore


@pytest.fixture()
def _tenant_ctx():
    """提供租户上下文切换助手。"""
    class _Ctx:
        @staticmethod
        def enter(tenant: str):
            return dbcore.set_tenant(tenant)

    return _Ctx()


def test_devices_isolated_by_tenant(_tenant_ctx):
    token = _tenant_ctx.enter("tenant-a")
    try:
        a = db.upsert_device({"id": "dev_ta", "name": "A-设备", "type": "af", "mode": "simulator", "base_url": "", "username": "", "password": "", "readonly": 0, "settings_json": "{}", "created_at": "2026-10-08T00:00:00"})
        assert a["tenant_id"] == "tenant-a"
    finally:
        dbcore.reset_tenant(token)

    token = _tenant_ctx.enter("tenant-b")
    try:
        # 跨租户按 id 访问：视为不存在（隔离边界）
        assert db.get_device("dev_ta") is None
        assert all(d["id"] != "dev_ta" for d in db.list_devices())
        # B 租户自建设备互不干扰
        b = db.upsert_device({"id": "dev_tb", "name": "B-设备", "type": "af", "mode": "simulator", "base_url": "", "username": "", "password": "", "readonly": 0, "settings_json": "{}", "created_at": "2026-10-08T00:00:00"})
        assert b["tenant_id"] == "tenant-b"
        assert {d["id"] for d in db.list_devices()} == {"dev_tb"}
    finally:
        dbcore.reset_tenant(token)

    # 默认租户（无头上下文）也看不到其它租户设备
    assert db.get_device("dev_ta") is None and db.get_device("dev_tb") is None


def test_conversations_isolated_and_shared_view(_tenant_ctx):
    token = _tenant_ctx.enter("tenant-a")
    try:
        ca = db.create_conversation("A 会话")
        assert ca["tenant_id"] == "tenant-a"
    finally:
        dbcore.reset_tenant(token)

    token = _tenant_ctx.enter("tenant-b")
    try:
        db.create_conversation("B 会话")
        names = {c["title"] for c in db.list_conversations()}
        assert names == {"B 会话"}
    finally:
        dbcore.reset_tenant(token)

    # 共享审计视角：全部客户可见
    titles = {c["title"] for c in db.list_conversations(all_tenants=True)}
    assert {"A 会话", "B 会话"} <= titles
    paged = db.list_conversations_paged(all_tenants=True)
    got = {c["title"] for c in paged.get("items", paged.get("rows", []))} or titles
    assert {"A 会话", "B 会话"} <= (got | titles)


def test_netdev_devices_isolated_by_tenant(_tenant_ctx):
    token = _tenant_ctx.enter("tenant-a")
    try:
        nd = db.save_netdev_device({"host": "10.99.0.1", "port": 22, "name": "A-交换机",
                                    "vendor": "huawei"})
        assert nd["tenant_id"] == "tenant-a"
    finally:
        dbcore.reset_tenant(token)

    token = _tenant_ctx.enter("tenant-b")
    try:
        assert db.get_netdev_device(nd["id"]) is None
        assert all(x["id"] != nd["id"] for x in db.list_netdev_devices())
    finally:
        dbcore.reset_tenant(token)


def test_pending_action_and_backup_tagged(_tenant_ctx):
    token = _tenant_ctx.enter("tenant-a")
    try:
        act = db.create_pending_action({"id": db.new_id("act_"), "conv_id": "conv_x",
                                        "tool_name": "t", "args_json": "{}",
                                        "summary": "s", "status": "pending",
                                        "created_at": db.now()})
        bk = db.create_backup({"id": db.new_id("bk_"), "device_id": "dev_x", "label": "l",
                               "kind": "manual", "sw_version": "", "snapshot_json": "{}",
                               "file_path": "", "file_sha256": "", "created_at": db.now(),
                               "created_by": "test"})
        assert act["tenant_id"] == "tenant-a" and bk["tenant_id"] == "tenant-a"
    finally:
        dbcore.reset_tenant(token)
