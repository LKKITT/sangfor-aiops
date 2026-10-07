"""AC 绑定变更单测：纯 IP/MAC 绑定走 ipmac-bindinfo（文档 4.4/4.6），update 按官方口径以「删除+重建」实现。

不触碰真实设备：mock _post 记录调用并断言接口路径 / body / 回滚行为。
"""
import pytest

from app.adapters.ac_rest import AcApiClient
from app.adapters.base import ChangeOp, DeviceError


@pytest.fixture()
def client():
    return AcApiClient(device_id="dev_test", device_name="AC-TEST",
                       base_url="http://10.0.0.1:9999", shared_key="key")


def _record_posts(client):
    """替换 _post 为记录器，返回调用列表 [(interface, body, method_override), ...]。"""
    calls: list[tuple[str, dict, str | None]] = []

    async def fake_post(interface, body, method_override=None):
        calls.append((interface, dict(body or {}), method_override))
        return {"code": 0, "message": "Success"}

    client._post = fake_post
    return calls


async def test_create_ipmac_binding_uses_ipmac_interface(client):
    calls = _record_posts(client)
    r = await client._apply_binding_change(ChangeOp(
        op="create", resource="binding",
        data={"ip": "10.68.5.16", "mac": "94-37-f7-98-93-64", "comment": "测试"}))
    assert r["ok"] is True
    assert calls == [("bindinfo/ipmac-bindinfo",
                      {"ip": "10.68.5.16", "mac": "94-37-f7-98-93-64", "desc": "测试"}, None)]


async def test_create_composes_purpose_into_desc(client):
    """描述与绑定目的组合落库到唯一的 desc 字段（卡片两字段 → 接口单字段）。"""
    calls = _record_posts(client)
    await client._apply_binding_change(ChangeOp(
        op="create", resource="binding",
        data={"ip": "10.68.5.16", "mac": "94-37-f7-98-93-64",
              "comment": "办公区打印机", "purpose": "固定 IP 准入"}))
    assert calls[0][1]["desc"] == "办公区打印机；目的：固定 IP 准入"
    # 仅目的无描述时也要落 desc
    calls.clear()
    await client._apply_binding_change(ChangeOp(
        op="create", resource="binding",
        data={"ip": "10.68.5.17", "mac": "AA-BB-CC-DD-EE-01", "purpose": "访客终端"}))
    assert calls[0][1]["desc"] == "目的：访客终端"


async def test_create_user_binding_still_uses_user_interface(client):
    calls = _record_posts(client)
    r = await client._apply_binding_change(ChangeOp(
        op="create", resource="binding",
        data={"user": "tom", "ip": "1.1.1.1", "mac": "11-11-11-11-11-11"}))
    assert r["ok"] is True
    assert calls[0][0] == "bindinfo/user-bindinfo"
    assert calls[0][1]["addr"] == "1.1.1.1+11-11-11-11-11-11"
    assert calls[0][1]["addr_type"] == "ipmac"


async def test_delete_ipmac_binding_uses_method_override(client):
    calls = _record_posts(client)
    r = await client._apply_binding_change(ChangeOp(
        op="delete", resource="binding",
        data={"ip": "10.68.5.16", "mac": "94-37-f7-98-93-64", "binding_type": "ipmac"}))
    assert r["ok"] is True
    assert calls == [("bindinfo/ipmac-bindinfo", {"ip": "10.68.5.16"}, "DELETE")]


async def test_update_ipmac_binding_is_delete_then_create(client):
    calls = _record_posts(client)
    r = await client._apply_binding_change(ChangeOp(
        op="update", resource="binding",
        data={"ip": "10.68.5.16", "mac": "AA-BB-CC-DD-EE-FF", "binding_type": "ipmac",
              "comment": "换网卡"},
        current={"ip": "10.68.5.16", "mac": "94-37-f7-98-93-64", "user": ""}))
    assert r["ok"] is True
    assert "删除+重建" in r["message"]
    # 第一笔：删旧（按 ip）；第二笔：建新（新 mac）
    assert calls[0] == ("bindinfo/ipmac-bindinfo", {"ip": "10.68.5.16"}, "DELETE")
    assert calls[1] == ("bindinfo/ipmac-bindinfo",
                        {"ip": "10.68.5.16", "mac": "AA-BB-CC-DD-EE-FF", "desc": "换网卡"}, None)


async def test_update_rolls_back_old_binding_when_create_fails(client):
    calls = _record_posts(client)

    async def failing_post(interface, body, method_override=None):
        calls.append((interface, dict(body or {}), method_override))
        if len(calls) == 2:  # 第二笔 POST = 建新绑定（第一笔删旧，第三笔回滚）
            raise DeviceError("校验参数失败!")
        return {"code": 0, "message": "Success"}

    client._post = failing_post
    with pytest.raises(DeviceError) as ei:
        await client._apply_binding_change(ChangeOp(
            op="update", resource="binding",
            data={"ip": "10.68.5.16", "mac": "AA-BB-CC-DD-EE-FF", "binding_type": "ipmac"},
            current={"ip": "10.68.5.16", "mac": "94-37-f7-98-93-64", "user": ""}))
    assert "已回滚保留原绑定" in str(ei.value)
    # 调用序：删旧 → 建新(失败) → 回滚重建旧绑定
    assert calls[0][1] == {"ip": "10.68.5.16"} and calls[0][2] == "DELETE"
    assert calls[2][1]["mac"] == "94-37-f7-98-93-64"
    assert calls[2][1]["ip"] == "10.68.5.16"


async def test_update_user_binding_delete_then_create_via_user_interface(client):
    calls = _record_posts(client)
    r = await client._apply_binding_change(ChangeOp(
        op="update", resource="binding",
        data={"user": "tom", "ip": "2.2.2.2", "mac": "22-22-22-22-22-22"},
        current={"ip": "1.1.1.1", "mac": "11-11-11-11-11-11", "user": "tom"}))
    assert r["ok"] is True
    assert calls[0][0] == "bindinfo/user-bindinfo" and calls[0][2] == "DELETE"
    assert calls[1][0] == "bindinfo/user-bindinfo"
    assert calls[1][1]["addr"] == "2.2.2.2+22-22-22-22-22-22"


async def test_snapshot_tolerates_unenumerable_bindings(client):
    """AC 快照：绑定枚举因 search 必选失败时留空，快照/备份/变更计划不整体失败。"""
    from app.adapters.base import DeviceError

    async def fail_bindings():
        raise DeviceError("AC 绑定查询需要提供关键词（用户名 / IP / MAC，支持模糊匹配），官方接口不支持查询全部")

    async def ok_list(*a, **k):
        return []

    async def ok_status():
        class S:
            sw_version, model = "AC12.0.40", "AC"
        return S()

    client.get_user_bindings = fail_bindings
    client.get_net_policies = ok_list
    client.get_flux_policies = ok_list
    client.get_online_users = ok_list
    client.get_throughput = ok_list
    client.get_app_rank = ok_list
    client.get_status = ok_status
    snap = await client.snapshot_config()
    assert snap["user_bindings"] == []          # 如实留空，不抛异常
    assert snap["meta"]["device_type"] == "ac"  # 其余快照内容正常
