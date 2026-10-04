"""SCP 云计算平台适配器测试：MockTransport 模拟 OpenAPI（不打真网）。"""

import httpx
import pytest

from app.adapters.base import ChangeOp, DeviceError
from app.adapters.scp_rest import ScpApiClient
from app.services.analyzer import run_checks


def _scp_client(handler) -> ScpApiClient:
    return ScpApiClient("dev_scp", "SCP测试", "https://10.1.1.1", "AK123", "SK456",
                        transport=httpx.MockTransport(handler))


def _ok(data):
    return httpx.Response(200, json={"code": 0, "message": "", "data": data})


def _page(items, next_page=""):
    return {"total_size": len(items), "page_num": 0, "page_size": 100,
            "next_page_num": next_page, "data": items}


PLATFORM = {"version": "6.10.0", "build_time": "2024", "manage_mode": "private_cloud",
            "dcluster_info": {"mode": "cluster", "cluster_ip": "10.1.1.1", "cluster_port": 4430}}
OVERVIEW = {
    "physical_resources": [
        {"name": "cpu", "total": 100000, "used": 25000, "unit": "mhz"},
        {"name": "memory", "total": 1000000, "used": 500000, "unit": "mb"},
        {"name": "storage", "total": 10000000, "used": 9000000, "unit": "mb"},
    ],
    "host": {"total": 3, "online_count": 2, "offline_count": 1, "alarm_count": 0},
    "server": {"total": 20, "running_count": 15, "offline_count": 5, "alarm_count": 1},
}


def test_signature_header_format():
    """签名头按官方 JS 示例格式（Credential/SignedHeaders=path;x-amz-date）。"""
    c = _scp_client(lambda req: _ok(PLATFORM))
    auth, amz = c._auth_header("GET", "/janus/20180725/platform")
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AK123/")
    assert "/cn-south-1/open-api/aws4_request" in auth
    assert "SignedHeaders=path;x-amz-date" in auth
    assert len(amz) == 16 and amz.endswith("Z")


def test_request_carries_signature_and_unwraps_data():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers.get("Authorization", "")
        seen["amz"] = req.headers.get("X-Amz-Date", "")
        return _ok(PLATFORM)

    c = _scp_client(handler)
    import asyncio
    got = asyncio.run(c._request("GET", "/janus/20180725/platform"))
    assert got["version"] == "6.10.0"
    assert seen["auth"].startswith("AWS4-HMAC-SHA256 ")
    assert seen["amz"]


def test_login_via_platform():
    c = _scp_client(lambda req: _ok(PLATFORM))
    import asyncio
    assert asyncio.run(c.login()) is True


def test_login_failure_raises():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"code": 401, "message": "unauthorized"})
    c = _scp_client(handler)
    import asyncio
    with pytest.raises(DeviceError, match="认证失败"):
        asyncio.run(c.login())


def test_get_status_from_overview():
    def handler(req: httpx.Request) -> httpx.Response:
        if "overview" in str(req.url):
            return _ok(OVERVIEW)
        return _ok(PLATFORM)
    c = _scp_client(handler)
    import asyncio
    st = asyncio.run(c.get_status())
    assert st.sw_version == "6.10.0"
    assert st.cpu_usage == 25.0 and st.memory_usage == 50.0 and st.disk_usage == 90.0
    assert st.session_count == 15 and st.session_capacity == 20
    assert st.extra["hosts_online"] == 2 and st.extra["servers_total"] == 20


def test_paginated_list_aggregation():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok(_page([{"id": f"h{i}"} for i in range(100)], next_page="1"))
        return _ok(_page([{"id": "h100"}]))
    c = _scp_client(handler)
    import asyncio
    hosts = asyncio.run(c.get_scp_hosts())
    assert calls["n"] == 2 and len(hosts) == 101


def test_readonly_boundary():
    """六个配置查询返回空；apply_change 一律拒绝。"""
    c = _scp_client(lambda req: _ok([]))
    import asyncio
    assert asyncio.run(c.get_nat_rules()) == []
    assert asyncio.run(c.get_acl_rules()) == []
    assert asyncio.run(c.get_user_bindings()) == []
    assert asyncio.run(c.get_static_routes()) == []
    assert asyncio.run(c.get_network_objects()) == []
    assert asyncio.run(c.get_services()) == []
    with pytest.raises(DeviceError, match="只读"):
        asyncio.run(c.apply_change(ChangeOp(op="create", resource="vm", target_id="", data={})))
    assert set(c.capability_gaps) >= {"nat_rules", "acl_rules", "user_bindings"}


def test_snapshot_config_sections():
    """结构化快照：平台/集群/物理机/网口功能 IP/虚拟机网卡/桥接端口组。"""
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "overview" in url:
            return _ok(OVERVIEW)
        if "/hosts/h1/interfaces" in url:
            return _ok(_page([{"id": "eth0", "name": "eth0", "functions": ["mgmt", "business"],
                               "status": 1, "ip": "10.1.1.10", "netmask": "255.255.255.0",
                               "gateway": "10.1.1.1", "mac": "aa:bb:cc", "speed": 10000,
                               "communication_interface": [{"function": "mgmt", "ip": "10.1.1.10"}]}]))
        if "/servers" in url:
            return _ok(_page([{"id": "vm-1", "name": "web01", "status": "running",
                               "ips": ["192.168.1.10"], "host_id": "h1", "host_name": "10.1.1.10",
                               "networks": [{"mac_address": "aa:bb", "ip_address": "192.168.1.10",
                                             "vif_id": "net0", "subnet_name": "业务网"}],
                               "cores": 4, "memory_mb": 8192, "storage_mb": 102400}]))
        if "classic-bvswitches" in url:
            return _ok(_page([{"id": "bvs1", "name": "业务端口组",
                               "vlan_group": [{"name": "vg1", "type": "access", "vlan_id": 100,
                                               "links": [{"name": "l1", "type": "vm", "ipv4": "192.168.1.10"}]}],
                               "phy_if": [{"port": "eth0", "host_id": "h1", "status": 1}]}]))
        if "/clusters" in url:
            return _ok(_page([{"id": "c1", "name": "集群A", "version": "8.0.0", "type": "hci",
                               "status": "normal", "cpu": {"ratio": 30},
                               "memory": {"ratio": 40}, "storage": {"ratio": 50}}]))
        if "/hosts" in url:
            return _ok(_page([{"id": "h1", "name": "10.1.1.10", "status": "running",
                               "cluster_id": "c1", "cluster_name": "集群A",
                               "cpu": {"ratio": 30, "total_mhz": 1, "used_mhz": 0},
                               "memory": {"ratio": 40}, "storage": {"total_mb": 1}}]))
        if "/storages" in url:
            return _ok(_page([{"id": "s1", "name": "存储池", "type": "hci", "status": "normal",
                               "total_mb": 1000, "used_mb": 100}]))
        return _ok(PLATFORM)

    c = _scp_client(handler)
    import asyncio
    snap = asyncio.run(c.snapshot_config())
    assert snap["meta"]["device_type"] == "scp"
    assert snap["scp_platform"]["version"] == "6.10.0"
    assert snap["scp_clusters"][0]["name"] == "集群A"
    assert snap["scp_host_interfaces"][0]["interfaces"][0]["functions"] == ["mgmt", "business"]
    assert snap["scp_vms"][0]["networks"][0]["subnet_name"] == "业务网"
    assert snap["scp_bvswitches"][0]["vlan_group"][0]["vlan_id"] == 100
    assert snap["interfaces"] == []   # AF 语义空节保持机制通用


def test_scp_checkup_rules():
    """体检：集群/物理机/虚拟机/存储资源规则。"""
    snap = {"meta": {"device_type": "scp"},
            "scp_clusters": [{"name": "集群A", "status": "normal",
                              "cpu": {"total_mhz": 100, "used_mhz": 92},
                              "memory": {"ratio": 40}, "storage": {"ratio": 91}}],
            "scp_hosts": [{"name": "10.0.0.1", "status": "running", "cpu": {"ratio": 92},
                           "memory": {"ratio": 50}, "alarm_count": 2}],
            "scp_vms": [{"name": "vm-bad", "status": "alert", "alarm": {"alarm": True}},
                        {"name": "vm-old", "status": "stopped", "is_stopped": 1,
                         "shutdown_duration": 900000}],
            "scp_storages": [{"name": "存储池", "status": "normal",
                              "total_mb": 1000, "used_mb": 860}]}
    rep = run_checks(snap, {"cpu_usage": 30}, "scp")
    titles = [i["title"] for i in rep["items"]]
    assert any("集群「集群A」CPU使用率 92.0%" in t for t in titles)
    assert any("集群「集群A」存储使用率 91.0%" in t for t in titles)
    assert any("物理机「10.0.0.1」CPU" in t for t in titles)
    assert any("2 条未处理告警" in t for t in titles)
    assert any("vm-bad" in t for t in titles)
    assert any("关机超过 7 天" in t for t in titles)
    assert any("存储「存储池」使用率 86.0%" in t for t in titles)


def test_scp_upgrade_path_direct():
    from app.services.knowledge import versions as kb
    path = kb.upgrade_path("scp", "6.7.0", "6.10.0")
    assert path["hops"] == ["6.10.0"] and path["direct_upgrade"] is True
    assert any("前置检测" in n for n in path["notes"])
    assert kb.product_of("SCP6.7.33") == "scp"
    assert kb.product_of("6.10.0") == ""   # 无前缀不误判


def test_scp_report_section():
    """备份报告 SCP 章节：版本/集群/物理机/虚拟机/网口/端口组/存储。"""
    from app.services.report_generator import render_scp_section
    snapshot = {
        "meta": {"device_name": "SC-10.68.10.2", "device_type": "scp", "sw_version": "6.10.0"},
        "scp_platform": {"version": "6.10.0", "manage_mode": "private_cloud",
                         "dcluster_info": {"cluster_ip": "10.68.10.2"}, "maintain_mode": 0},
        "scp_clusters": [{"name": "集群A", "version": "8.0.0", "type": "hci", "status": "normal",
                          "cpu": {"total_mhz": 100, "used_mhz": 30},
                          "memory": {"ratio": 40}, "storage": {"ratio": 50}}],
        "scp_hosts": [{"name": "10.68.10.11", "cluster_name": "集群A", "status": "running",
                       "memory": {"ratio": 40},
                       "storage": {"total_mb": 10240000}, "alarm_count": 1,
                       "cpu": {"ratio": 30, "type": "Xeon"}}],
        "scp_vms": [{"name": "web01", "status": "running", "ips": ["192.168.1.10"],
                     "host_name": "10.68.10.11", "os_name": "", "os_type": "Linux",
                     "cores": 4, "memory_mb": 8192, "storage_mb": 102400,
                     "cpu_status": {"ratio": 12}, "memory_status": {"ratio": 30}}],
        "scp_host_interfaces": [{"host_name": "10.68.10.11",
                                 "interfaces": [{"name": "eth0", "functions": ["mgmt"],
                                                 "ip": "10.68.10.11", "gateway": "10.68.10.1",
                                                 "mac": "aa:bb", "speed": 10000,
                                                 "communication_interface": [
                                                     {"function": "mgmt", "ip": "10.68.10.11"}]}]}],
        "scp_bvswitches": [{"name": "业务交换机", "vlan_group": [
            {"name": "vg1", "type": "access", "vlan_id": 100,
             "links": [{"ipv4": "192.168.1.10"}]}]}],
        "scp_storages": [{"name": "存储池", "type": "hci", "status": "normal",
                          "total_mb": 10240000, "used_mb": 5120000, "ratio": 50.0}],
    }
    html = render_scp_section(snapshot, {"cpu_usage": 25, "memory_usage": 50,
                                         "extra": {"storage_ratio": 50}})
    for token in ("版本信息", "集群资源情况", "物理机情况", "虚拟机情况",
                  "物理机网口与功能 IP", "桥接网卡与端口组", "存储资源",
                  "6.10.0", "集群A", "web01", "192.168.1.10", "业务交换机"):
        assert token in html, f"报告缺少：{token}"
    # ratio 折算：total 100/used 30 → 30.0%
    assert "30.0%" in html
    # OS 归一化：os_name 空回退 os_type
    assert "Linux" in html
