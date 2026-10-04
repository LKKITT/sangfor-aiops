"""新增能力回归：对象/服务分析、设置存储、快照导出与迁移语义。"""


from app import db
from app.services.analyzer import check_objects, check_services


def test_unreferenced_object_detected():
    objects = [{"id": "o1", "name": "废弃网段", "type": "ipgroup",
                "members": "192.168.200.0/24", "comment": ""}]
    acl = [{"src_addr": "财务网段", "dst_addr": "any", "service": "any"}]
    nat = [{"src_addr": "any", "dst_addr": "any", "service": "any", "translated_addr": ""}]
    items = check_objects(objects, acl, nat)
    assert any(i.check_id == "OBJ_UNREFERENCED" and "o1" in i.rule_ids for i in items)


def test_referenced_object_no_finding():
    objects = [{"id": "o1", "name": "财务网段", "type": "ipgroup",
                "members": "192.168.10.0/24", "comment": ""}]
    acl = [{"src_addr": "财务网段", "dst_addr": "any", "service": "any"}]
    items = check_objects(objects, acl, [])
    assert not any(i.check_id == "OBJ_UNREFERENCED" for i in items)


def test_broad_object_flagged():
    objects = [{"id": "o1", "name": "全网段", "type": "ipgroup",
                "members": "0.0.0.0/0", "comment": ""}]
    acl = [{"src_addr": "全网段", "dst_addr": "any", "service": "any"}]
    items = check_objects(objects, acl, [])
    assert any(i.check_id == "OBJ_BROAD" and i.severity == "medium" for i in items)


def test_unreferenced_and_dangerous_service():
    services = [
        {"id": "s1", "name": "数据库端口", "protocol": "TCP", "ports": "3306,1433", "comment": ""},
        {"id": "s2", "name": "Web服务", "protocol": "TCP", "ports": "80,443", "comment": ""},
    ]
    acl = [{"src_addr": "any", "dst_addr": "any", "service": "Web服务"}]
    items = check_services(services, acl, [])
    assert any(i.check_id == "SVC_UNREFERENCED" and "s1" in i.rule_ids for i in items)
    assert any(i.check_id == "SVC_DANGEROUS_PORT" and "s1" in i.rule_ids for i in items)
    assert not any(i.check_id == "SVC_UNREFERENCED" and "s2" in i.rule_ids for i in items)


def test_settings_cookie_roundtrip():
    db.set_setting("sangfor_support_cookie", "SF_COOKIE=test123")
    assert db.get_setting("sangfor_support_cookie") == "SF_COOKIE=test123"
    db.set_setting("sangfor_support_cookie", "")
    assert db.get_setting("sangfor_support_cookie") == ""


def test_snapshot_export_contains_migration_sections(device_id):
    """快照导出必须包含可迁移的完整结构化配置（对象/服务/路由/策略/绑定）。"""
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        resp = c.post(f"/api/devices/{device_id}/backups", json={"label": "迁移测试备份"})
        backup = resp.json()
        resp = c.get(f"/api/devices/{device_id}/backups/{backup['id']}/snapshot/export")
        assert resp.status_code == 200
        assert "attachment" in resp.headers.get("content-disposition", "")
        snap = resp.json()
        for section in ("objects", "services", "static_routes", "nat_rules", "acl_rules", "user_bindings"):
            assert isinstance(snap.get(section), list) and snap[section], section
        assert "export_note" in snap["meta"]
        # 对象与策略的引用关系在导出物中可追溯（第三方迁移的语义基础）
        names = {o["name"] for o in snap["objects"]}
        assert "财务网段" in names
        src_refs = " ".join(str(r.get("src_addr", "")) for r in snap["acl_rules"])
        assert "财务网段" in src_refs


def test_restore_plan_orders_objects_before_rules():
    """恢复计划必须是依赖安全顺序：对象/服务先建，规则先改，对象最后删。"""
    from app.services.config_service import build_restore_plan
    device = {
        "objects": [{"id": "obj-001", "name": "旧成员", "members": "10.0.0.0/8"}],
        "services": [], "user_bindings": [],
        "acl_rules": [{"id": "acl-x", "name": "临时", "action": "allow"}],
        "nat_rules": [], "static_routes": [],
    }
    backup = {
        "meta": {},
        "objects": [{"id": "obj-001", "name": "旧成员", "members": "10.1.0.0/16"},
                    {"id": "obj-100", "name": "新对象", "members": "10.9.0.0/16"}],
        "services": [{"id": "svc-100", "name": "新服务", "protocol": "TCP", "ports": "7070"}],
        "user_bindings": [],
        "acl_rules": [{"id": "acl-y", "name": "新增策略", "action": "deny"}],
        "nat_rules": [], "static_routes": [],
    }
    plan = build_restore_plan(device, backup)
    order = [(op.resource, op.op) for op in plan.ops]
    # 对象创建必须在任何规则创建之前；对象删除必须在规则删除之后
    first_object_create = order.index(("object", "create"))
    first_acl_create = order.index(("acl", "create"))
    assert first_object_create < first_acl_create
    object_deletes = [i for i, (r, o) in enumerate(order) if r == "object" and o == "delete"]
    acl_deletes = [i for i, (r, o) in enumerate(order) if r == "acl" and o == "delete"]
    assert not object_deletes or not acl_deletes or min(object_deletes) > max(acl_deletes)
