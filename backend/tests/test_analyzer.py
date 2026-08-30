"""配置合理性分析引擎测试：构造典型风险配置，断言全部检出且无误报。"""
from app.services.analyzer import (
    check_acl_rules, check_bindings, check_nat_rules, check_routes, check_status, run_checks,
)


def acl(**kw):
    base = {"id": "x", "name": "n", "enabled": True, "src_zone": "trust", "dst_zone": "untrust",
            "src_addr": "192.168.1.0/24", "dst_addr": "any", "service": "HTTP/HTTPS",
            "app": "any", "action": "allow", "hit_count": 10, "log": True, "comment": ""}
    base.update(kw)
    return base


def test_conflicting_deny_shadowed_by_allow():
    rules = [
        acl(id="r1", name="全放行", src_addr="192.168.0.0/16", service="any", action="allow"),
        acl(id="r2", name="禁财务", src_addr="192.168.10.0/24", service="any", action="deny"),
    ]
    items = check_acl_rules(rules)
    assert any(i.check_id == "ACL_CONFLICT" and i.severity == "high" for i in items)


def test_shadowed_same_action():
    rules = [
        acl(id="r1", name="大网段放行", src_addr="192.168.0.0/16"),
        acl(id="r2", name="小网段放行", src_addr="192.168.1.0/24"),
    ]
    items = check_acl_rules(rules)
    assert any(i.check_id == "ACL_SHADOW" for i in items)


def test_duplicate_rules():
    rules = [
        acl(id="r1", name="A"),
        acl(id="r2", name="A副本"),
    ]
    items = check_acl_rules(rules)
    assert any(i.check_id == "ACL_DUP" for i in items)


def test_any_any_allow_flagged_but_deny_not():
    rules = [acl(id="r1", name="全放行", src_zone="any", dst_zone="any",
                 src_addr="any", dst_addr="any", service="any")]
    assert any(i.check_id == "ACL_ANY_ANY" and i.severity == "high" for i in check_acl_rules(rules))
    rules[0]["action"] = "deny"
    assert not any(i.check_id == "ACL_ANY_ANY" for i in check_acl_rules(rules))


def test_dangerous_port_exposure():
    rules = [acl(id="r1", name="放行445", src_zone="untrust", dst_zone="dmz", log=False,
                 dst_addr="172.16.2.10", service="TCP/445")]
    items = check_acl_rules(rules)
    assert any(i.check_id == "ACL_DANGEROUS_PORT" and i.severity == "high" for i in items)
    assert any(i.check_id == "ACL_NO_LOG" for i in items)   # 未开日志也检出


def test_zombie_and_disabled():
    rules = [
        acl(id="z", name="零命中", hit_count=0),
        acl(id="d", name="停用", enabled=False),
    ]
    items = check_acl_rules(rules)
    assert any(i.check_id == "ACL_ZOMBIE" and "z" in i.rule_ids for i in items)
    assert any(i.check_id == "ACL_DISABLED" and "d" in i.rule_ids for i in items)


def test_normal_rules_no_false_positive():
    rules = [
        acl(id="ok1", name="正常放行", hit_count=100, log=True),
        acl(id="ok2", name="兜底拒绝", src_zone="any", dst_zone="any", src_addr="any",
            dst_addr="any", service="any", action="deny", hit_count=5),
    ]
    items = check_acl_rules(rules)
    assert not [i for i in items if i.severity == "high"]


def test_nat_checks():
    rules = [
        {"id": "n1", "name": "全网SNAT", "enabled": True, "type": "SNAT", "src_zone": "any",
         "dst_zone": "untrust", "src_addr": "any", "dst_addr": "any", "service": "any",
         "translated_addr": "1.2.3.4", "hit_count": 5, "log": True, "comment": ""},
        {"id": "n2", "name": "DMZ-SNAT", "enabled": True, "type": "SNAT", "src_zone": "dmz",
         "dst_zone": "untrust", "src_addr": "172.16.2.0/24", "dst_addr": "any", "service": "any",
         "translated_addr": "1.2.3.4", "hit_count": 5, "log": True, "comment": ""},
        {"id": "n3", "name": "发布RDP", "enabled": True, "type": "DNAT", "src_zone": "untrust",
         "dst_zone": "untrust", "src_addr": "any", "dst_addr": "1.2.3.4:13389",
         "service": "TCP/13389", "translated_addr": "172.16.2.5:3389",
         "translated_port": "3389", "hit_count": 1, "log": False, "comment": ""},
    ]
    items = check_nat_rules(rules)
    ids = {i.check_id for i in items}
    assert "NAT_BROAD" in ids
    assert "NAT_SHADOW" in ids
    assert "NAT_MGMT_EXPOSE" in ids


def test_status_thresholds():
    items = check_status({"cpu_usage": 92, "memory_usage": 75, "disk_usage": 50,
                          "mbuf_usage": 80, "session_count": 180000, "session_capacity": 200000})
    ids = {i.check_id: i.severity for i in items}
    assert ids.get("RES_CPU_USAGE") == "high"
    assert ids.get("RES_MEMORY_USAGE") == "medium"
    assert "RES_DISK_USAGE" not in ids
    assert ids.get("RES_MBUF_USAGE") == "high"
    assert ids.get("RES_SESSION") == "medium"


def test_bindings_and_routes():
    bindings = [
        {"id": "b1", "user": "A", "ip": "192.168.1.5", "mac": "AA:00:00:00:00:01",
         "binding_type": "static", "enabled": True, "comment": ""},
        {"id": "b2", "user": "B", "ip": "192.168.1.5", "mac": "AA:00:00:00:00:02",
         "binding_type": "static", "enabled": True, "comment": ""},
        {"id": "b3", "user": "C", "ip": "192.168.1.6", "mac": "",
         "binding_type": "static", "enabled": True, "comment": ""},
    ]
    items = check_bindings(bindings)
    ids = {i.check_id for i in items}
    assert "BIND_DUP_IP" in ids and "BIND_NO_MAC" in ids

    routes = [{"id": "r", "dst": "10.0.0.0/8", "enabled": True}]
    assert any(i.check_id == "ROUTE_NO_DEFAULT" and i.severity == "high"
               for i in check_routes(routes))


def test_run_checks_scoring_and_order():
    snapshot = {
        "meta": {"device_name": "t", "sw_version": "8.0.85"},
        "acl_rules": [acl(id="bad", name="全放行", src_zone="any", dst_zone="any",
                          src_addr="any", dst_addr="any", service="any", log=False)],
        "nat_rules": [], "user_bindings": [],
        "static_routes": [{"id": "r", "dst": "0.0.0.0/0", "enabled": True}],
    }
    report = run_checks(snapshot, {"cpu_usage": 30, "memory_usage": 40, "disk_usage": 50,
                                   "mbuf_usage": 20, "session_count": 100, "session_capacity": 200000})
    assert report["score"] < 100
    assert report["counts"]["high"] >= 1
    sevs = [i["severity"] for i in report["items"]]
    assert sevs == sorted(sevs, key={"high": 0, "medium": 1, "low": 2}.get)
