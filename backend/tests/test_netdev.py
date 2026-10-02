"""网络设备管理测试：厂商档案、批量并行编排（假 SSH）、进度落库、API 冒烟。"""
import asyncio
import re

import httpx
import pytest

from app import db
from app.services import netdev_service


@pytest.fixture()
def nd_device():
    rec = db.save_netdev_device({
        "name": "核心交换机01", "vendor": "huawei", "host": "10.20.1.1", "port": 22,
        "username": "admin", "password": "Huawei@123", "group_name": "总部"})
    yield rec
    db.delete_netdev_device(rec["id"])


# ---------- 设备 CRUD ----------

def test_device_crud_and_unique_host_port():
    d1 = db.save_netdev_device({"name": "A", "vendor": "h3c", "host": "10.99.0.1", "port": 22})
    # 同 host:port 再次保存 → 更新而非新建（批量导入幂等）
    d2 = db.save_netdev_device({"name": "A改名", "vendor": "h3c", "host": "10.99.0.1", "port": 22})
    assert d2["id"] == d1["id"] and d2["name"] == "A改名"
    assert len([d for d in db.list_netdev_devices() if d["host"] == "10.99.0.1"]) == 1
    db.delete_netdev_device(d1["id"])
    assert db.get_netdev_device(d1["id"]) is None


def test_vendor_profiles():
    assert netdev_service.profile_of("huawei").paging_cmd == "screen-length 0 temporary"
    assert netdev_service.profile_of("cisco").paging_cmd == "terminal length 0"
    assert netdev_service.profile_of("未知厂家").paging_cmd == netdev_service.profile_of("other").paging_cmd


def test_extended_vendor_profiles():
    """新增厂家档案：分页/版本命令与提示符特征齐备。"""
    p = netdev_service.profile_of
    assert p("juniper").paging_cmd == "set cli screen-length 0"
    assert p("aruba").paging_cmd == "no paging"
    assert p("mikrotik").paging_cmd == ""                       # 无分页概念
    assert p("mikrotik").version_cmd == "/system resource print"
    assert p("nokia").paging_cmd == "environment no more"
    assert p("dell").version_cmd == p("tplink").version_cmd == "show version"
    for v in ("juniper", "aruba", "dell", "tplink", "mikrotik", "nokia"):
        assert p(v).prompt_re   # 提示符正则非空


def test_extended_vendors_accepted_by_api(nd_device):
    """API 白名单与下拉接口同步收录新厂家。"""
    import httpx
    from app.main import app

    async def _call():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            vs = [v["vendor"] for v in (await ac.get("/api/netdev/vendors")).json()]
            r = await ac.post("/api/netdev/devices", json={
                "name": "Juniper核心", "vendor": "juniper", "host": "10.88.0.1",
                "username": "admin", "password": "x"})
            body = r.json()
            for it in (await ac.get("/api/netdev/devices")).json():
                if it["host"] == "10.88.0.1":
                    await ac.delete(f"/api/netdev/devices/{it['id']}")
            return vs, r.status_code, body

    vs, status, body = asyncio.run(_call())
    assert {"juniper", "aruba", "dell", "tplink", "mikrotik", "nokia"} <= set(vs)
    assert status == 200 and body["vendor"] == "juniper"


# ---------- 批量并行执行（假 SSH） ----------

@pytest.fixture()
def fake_ssh(monkeypatch):
    """替换 run_commands：可按设备名控制成功/失败与延迟。"""
    calls: list[tuple[str, list[str]]] = []

    async def fake_run(device, commands, timeout=30, idle_window=None):
        calls.append((device["name"], list(commands)))
        await asyncio.sleep(0.02)
        if "坏" in device["name"]:
            return netdev_service.SSHResult(ok=False, error="SSH 认证失败：用户名或口令错误")
        return netdev_service.SSHResult(ok=True, output=f"output-of-{device['name']}", duration=0.02)

    monkeypatch.setattr(netdev_service, "run_commands", fake_run)
    return calls


@pytest.mark.asyncio
async def test_batch_task_parallel_with_progress(nd_device, fake_ssh):
    d2 = db.save_netdev_device({"name": "接入交换机", "vendor": "h3c",
                                "host": "10.20.1.2", "password": "x"})
    d3 = db.save_netdev_device({"name": "坏设备", "vendor": "cisco",
                                "host": "10.20.1.3", "password": "x"})
    try:
        task_id = netdev_service.start_batch_task("巡检", [nd_device, d2, d3],
                                                  ["display version", "display clock"],
                                                  timeout=10)
        for _ in range(100):
            task = db.get_netdev_task(task_id)
            if task["status"] != "running":
                break
            await asyncio.sleep(0.02)
        task = db.get_netdev_task(task_id)
        assert task["status"] == "done"   # 两台成功一台失败：任务完成
        assert {i["device_name"] for i in task["items"]} == \
            {"核心交换机01", "坏设备", "接入交换机"}
        assert len(task["items"]) == 3
        by_name = {i["device_name"]: i for i in task["items"]}
        assert by_name["核心交换机01"]["status"] == "ok"
        assert by_name["核心交换机01"]["output"] == "output-of-核心交换机01"
        assert by_name["坏设备"]["status"] == "failed"
        assert "认证失败" in by_name["坏设备"]["error"]
        # 每台设备都收到完整命令列表
        assert len(fake_ssh) == 3
        assert {tuple(c) for _, c in fake_ssh} == {("display version", "display clock")}
    finally:
        db.delete_netdev_device(d2["id"])
        db.delete_netdev_device(d3["id"])


@pytest.mark.asyncio
async def test_batch_task_failed_all_marks_failed(nd_device, fake_ssh):
    bad = db.save_netdev_device({"name": "坏设备2", "vendor": "cisco",
                                 "host": "10.20.1.9", "password": "x"})
    try:
        task_id = netdev_service.start_batch_task("全失败", [bad], ["show version"], timeout=5)
        for _ in range(100):
            task = db.get_netdev_task(task_id)
            if task["status"] != "running":
                break
            await asyncio.sleep(0.02)
        assert db.get_netdev_task(task_id)["status"] == "failed"   # 全失败：任务标 failed
    finally:
        db.delete_netdev_device(bad["id"])


# ---------- API 冒烟 ----------

@pytest.mark.asyncio
async def test_netdev_api_smoke(nd_device):
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        devices = (await ac.get("/api/netdev/devices")).json()
        assert any(d["id"] == nd_device["id"] for d in devices)
        got = next(d for d in devices if d["id"] == nd_device["id"])
        assert "password" not in got and got["has_password"] is True   # 密码不回传

        vendors = (await ac.get("/api/netdev/vendors")).json()
        assert any(v["vendor"] == "huawei" for v in vendors)

        batch = await ac.post("/api/netdev/devices/batch", json={"devices": [
            {"name": "批量1", "host": "10.77.0.1", "vendor": "h3c", "password": "x"},
            {"name": "批量2", "host": "10.77.0.2", "vendor": "bad", "password": "x"},
            {"name": "批量3", "host": "", "vendor": "h3c"},
        ]})
        body = batch.json()
        assert body["saved"] == 1 and len(body["failed"]) == 2
        assert all("password" not in d for d in body["devices"])

        r = await ac.post("/api/netdev/execute", json={"device_ids": [], "commands": ["x"]})
        assert r.status_code == 400
        r2 = await ac.post("/api/netdev/execute", json={
            "device_ids": [nd_device["id"]], "commands": []})
        assert r2.status_code == 400

        detail = await ac.get(f"/api/netdev/tasks/nonexistent")
        assert detail.status_code == 404

        # 清理批量导入
        for d in (await ac.get("/api/netdev/devices")).json():
            if d["host"].startswith("10.77."):
                await ac.delete(f"/api/netdev/devices/{d['id']}")


# ---------- 回归：SSH 参数/异常兜底（测试接口 500）与 WS 控制台 ----------

@pytest.mark.asyncio
async def test_run_commands_unexpected_exception_becomes_friendly_failure(nd_device, monkeypatch):
    """未预期异常（如传错 asyncssh 参数的 TypeError）转 ok=False 友好错误，不再逃逸成 HTTP 500。"""
    class _BrokenAsyncssh:
        class misc:
            class PermissionDenied(Exception):
                pass
        class Error(Exception):
            pass

        @staticmethod
        async def connect(**kwargs):
            raise TypeError(
                "SSHClientConnectionOptions.prepare() got an unexpected keyword argument")

    monkeypatch.setattr(netdev_service, "asyncssh", _BrokenAsyncssh, raising=False)
    monkeypatch.setattr(netdev_service, "SSH_AVAILABLE", True)
    result = await netdev_service.run_commands(nd_device, ["display version"], timeout=5)
    assert result["ok"] is False
    assert "SSH 执行异常" in result["error"]


def test_connect_helper_has_no_invalid_kwargs():
    """asyncssh_connect 源码不得再出现错误参数名（回归 encrypt_algs 拼写错误）。"""
    import inspect
    from app.api import netdev as netdev_api
    src = inspect.getsource(netdev_api.asyncssh_connect)
    assert "encrypt_algs" not in src
    # 参数白名单：仅 asyncssh.connect 真实接受的连接参数
    for kw in re.findall(r"(\w+)=", src):
        assert kw in {"device", "known_hosts", "host", "port", "username",
                      "password", "timeout"}, f"可疑参数：{kw}"


def test_run_commands_accepts_real_asyncssh_when_installed():
    """装了 asyncssh 的解释器上：run_commands 对不可达主机的连接失败也走友好降级。"""
    pytest.importorskip("asyncssh")
    import asyncio as _asyncio
    result = _asyncio.run(netdev_service.run_commands(
        {"id": "x", "name": "不可达", "vendor": "huawei", "host": "10.255.255.1",
         "port": 22, "username": "a", "password": "b"}, ["display version"], timeout=3))
    assert result["ok"] is False and "超时" in result["error"] or "失败" in result["error"]


# ---------- 回归：asyncssh 终端参数名（term_width → term_size） ----------

def test_create_process_kwargs_match_real_signature():
    """源码中 create_process 的终端参数必须是 asyncssh 真实接受的。

    create_process(**kwargs) 透传给 create_session——用真实签名 bind 校验，
    防止再次出现 term_width/term_height 这类静默拼错的参数（装 asyncssh 才校验）。
    """
    asyncssh = pytest.importorskip("asyncssh")
    import inspect
    import re as _re
    from app.api import netdev as netdev_api
    from app.services import netdev_service
    sig = asyncssh.SSHClientConnection.create_session.__wrapped__.__signature__ \
        if hasattr(asyncssh.SSHClientConnection.create_session, "__wrapped__") \
        else inspect.signature(asyncssh.SSHClientConnection.create_session)

    for module in (netdev_service, netdev_api):
        src = inspect.getsource(module)
        for call in _re.findall(r"create_process\(([^)]*)\)", src, _re.S):
            kwargs = dict(_re.findall(r"(\w+)=([^,)]+)", call))
            kwargs.pop("self", None)
            # create_process(**kwargs) 透传 create_session：仅校验参数名真实存在
            # （term_width/term_height 之类拼错会被拦下；必需参数由 asyncssh 内部补齐）
            unknown = set(kwargs) - set(sig.parameters)
            assert not unknown,                 f"{module.__name__} create_process 未知参数：{unknown}（合法：{sorted(sig.parameters)}）"


# ---------- 编辑已有设备（按 id 更新 + 口令留空保留） ----------

@pytest.mark.asyncio
async def test_edit_device_by_id_and_password_preserved(nd_device):
    import httpx
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        # 编辑：改名改 IP，口令留空 → 保留原口令；id 不变
        r = await ac.post("/api/netdev/devices", json={
            "device_id": nd_device["id"], "name": "核心交换机01-改",
            "vendor": "huawei", "host": "10.20.1.99", "port": 22,
            "username": "admin", "password": "", "enable_password": ""})
        body = r.json()
        assert r.status_code == 200 and body["id"] == nd_device["id"]
        assert body["name"] == "核心交换机01-改" and body["host"] == "10.20.1.99"
        got = db.get_netdev_device(nd_device["id"])
        assert got["password"] == "Huawei@123"          # 原口令保留
        assert got["enable_password"] == ""
        # 编辑不存在的设备：404
        r2 = await ac.post("/api/netdev/devices", json={
            "device_id": "nd_nonexist", "name": "x", "host": "1.2.3.4",
            "vendor": "huawei", "username": "a"})
        assert r2.status_code == 404


# ---------- SSH 传统算法扩展（H3C 老固件 CBC）与导出端点 ----------

def test_ssh_connect_kwargs_keys_valid():
    """连接参数名必须是 asyncssh.connect 真实接受的（含算法扩展项）。"""
    asyncssh = pytest.importorskip("asyncssh")
    kwargs = netdev_service.ssh_connect_kwargs({"host": "x"})
    # 构造 options 对象即完成参数名校验（未知参数抛 TypeError）——encrypt_algs 事件的防线
    asyncssh.SSHClientConnectionOptions(**kwargs)
    print("options ok")
    # 传统算法以 "+" 前缀追加默认列表（老 H3C 仅支持 CBC/3DES）
    assert kwargs["encryption_algs"].startswith("+") and "aes128-cbc" in kwargs["encryption_algs"]


def test_export_devices_csv_roundtrip(nd_device):
    """导出 CSV 含口令列，表头与批量导入模板一致（导出→导入可往返）。"""
    import httpx
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    import asyncio as _asyncio

    async def _call():
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
            return (await ac.get("/api/netdev/devices/export")).json()

    body = _asyncio.run(_call())
    assert body["ok"] is True and body["filename"].endswith(".csv")
    rows = body["csv"].strip().splitlines()
    assert rows[0] == "名称,厂家,管理地址,端口,用户名,SSH口令,提权口令,分组,型号"
    assert any("Huawei@123" in r and nd_device["host"] in r for r in rows[1:])


@pytest.mark.asyncio
async def test_legacy_cipher_negotiation_end_to_end():
    """端到端验证传统算法扩展：起一个仅允许 aes128-cbc + group14-sha1 的 SSH 服务端，
    用 ssh_connect_kwargs（默认+传统扩展）连接必须协商成功——复刻 H3C 老固件场景。"""
    asyncssh = pytest.importorskip("asyncssh")

    class _Srv(asyncssh.SSHServer):
        def connection_made(self, conn):
            self._conn = conn

        def begin_auth(self, username):
            return False   # 免认证：本测试只验证算法协商

    def handle_process(process):
        process.stdout.write("LEGACY-OK\n")
        process.exit(0)

    server = await asyncssh.create_server(
        lambda: _Srv(), "127.0.0.1", 0,
        server_host_keys=[asyncssh.generate_private_key("ssh-rsa")],
        encryption_algs="aes128-cbc",                      # 仅 CBC：老 H3C 固件形态
        kex_algs="diffie-hellman-group14-sha1",
        process_factory=handle_process)
    port = server.sockets[0].getsockname()[1]
    device = {"id": "t", "name": "legacy", "host": "127.0.0.1", "port": port,
              "username": "u", "password": ""}
    try:
        conn = await asyncssh.connect(
            **netdev_service.ssh_connect_kwargs(device), client_keys=[])
        try:
            result = await conn.run("show version", check=False)
            assert "LEGACY-OK" in result.stdout
        finally:
            conn.close()
    except asyncssh.Error as e:
        raise AssertionError(f"传统算法协商失败（扩展未生效）：{e}")
    finally:
        server.close()
