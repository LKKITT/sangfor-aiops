"""模拟器与 AF REST 适配器测试。"""
import pytest
import httpx

from app.adapters.af_rest import AfRestClient
from app.adapters.base import ChangeOp, DeviceError
from app.adapters.factory import create_simulator_app
from app.adapters.simulator.state import STATE


@pytest.mark.asyncio
async def test_login_and_status(client):
    status = await client.get_status()
    assert status.sw_version.startswith("8.0.")
    assert 0 <= status.cpu_usage <= 100
    assert status.model


@pytest.mark.asyncio
async def test_bad_token_returns_auth_error():
    c = AfRestClientNoLogin("d1", "x", "http://s", "admin", "pw",
                            transport=httpx.ASGITransport(app=create_simulator_app(STATE)))
    with pytest.raises(DeviceError):
        await c.get_status()


class AfRestClientNoLogin(AfRestClient):
    async def login(self, force: bool = False) -> bool:
        self._token = "invalid-token"
        return True


@pytest.mark.asyncio
async def test_invalid_login_rejected(client):
    bad = AfRestClient("d1", "x", "http://s", "admin", "wrong-password",
                       transport=httpx.ASGITransport(app=create_simulator_app(STATE)))
    with pytest.raises(DeviceError):
        await bad.login()


@pytest.mark.asyncio
async def test_interfaces_and_snapshot(client):
    interfaces = await client.get_interfaces()
    assert any(i.name == "eth1" and i.zone == "untrust" for i in interfaces)
    snapshot = await client.snapshot_config()
    for section in ("interfaces", "static_routes", "nat_rules", "acl_rules", "user_bindings"):
        assert isinstance(snapshot[section], list) and snapshot[section]
    assert snapshot["meta"]["sw_version"].startswith("8.0.")


@pytest.mark.asyncio
async def test_nat_crud_roundtrip(client):
    created = await client.apply_change(ChangeOp(
        op="create", resource="nat",
        data={"name": "测试SNAT", "type": "SNAT", "src_zone": "trust", "dst_zone": "untrust",
              "src_addr": "192.168.50.0/24", "dst_addr": "any", "service": "any",
              "translated_addr": "202.96.1.2", "comment": "pytest"}))
    new_id = created["data"]["id"]
    rules = await client.get_nat_rules()
    assert any(r.id == new_id for r in rules)

    await client.apply_change(ChangeOp(op="update", resource="nat", target_id=new_id,
                                       data={"src_addr": "192.168.60.0/24"}))
    rules = await client.get_nat_rules()
    assert any(r.id == new_id and r.src_addr == "192.168.60.0/24" for r in rules)

    await client.apply_change(ChangeOp(op="delete", resource="nat", target_id=new_id))
    rules = await client.get_nat_rules()
    assert not any(r.id == new_id for r in rules)


@pytest.mark.asyncio
async def test_acl_update_and_duplicate_binding_rejected(client):
    await client.apply_change(ChangeOp(op="update", resource="acl", target_id="acl-006",
                                       data={"enabled": True}))
    rules = await client.get_acl_rules()
    assert any(r.id == "acl-006" and r.enabled for r in rules)

    with pytest.raises(DeviceError):
        await client.apply_change(ChangeOp(
            op="create", resource="binding",
            data={"user": "重复IP", "ip": "192.168.10.21", "mac": "aa:bb:cc:dd:ee:ff"}))


@pytest.mark.asyncio
async def test_config_file_roundtrip(client):
    data, filename = await client.backup_config_file()
    assert data.startswith(b"#SANGFOR-AF-CONF") and filename.endswith(".conf")
    result = await client.restore_config_file(data)
    assert result.get("accepted") is True
