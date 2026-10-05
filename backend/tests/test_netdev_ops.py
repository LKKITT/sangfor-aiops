"""网络设备工作台运维（netdev_ops）测试：配置体检规则引擎、备份创建/差异。

红线相关：netdev_ops_service 不提供任何恢复/下发接口——测试同时守卫这一约束。
"""
import json

import pytest

from app import db
from app.services import netdev_ops_service as ops

H3C_CONFIG = """
#
version 7.1.070, Release 6351P06
#
sysname GA-10F-5130-52S-1
#
telnet server enable
#
ftp server enable
#
snmp-agent community read public
#
local-user admin class manage
 password hash xxx
 service-type telnet http
#
user-interface vty 0 4
 authentication-mode scheme
 user-role network-admin
#
return
"""

HUAWEI_CONFIG_CLEAN = """
#
sysname CORE-SW
#
ssh server enable
stelnet server enable
#
aaa
 local-user admin password irreversible-cipher $1a$xyz
#
user-interface vty 0 4
 authentication-mode aaa
 acl 2001 inbound
#
info-center loghost 10.0.0.99
ntp-service unicast-server 10.0.0.100
#
return
"""


def test_checkup_h3c_risky_config():
    """典型带风险的 H3C 配置：telnet/ftp/snmp 公共团名/无日志主机/无 NTP/无 SSH 全部命中。"""
    r = ops.checkup_config(H3C_CONFIG, "h3c")
    titles = [i["title"] for i in r["items"]]
    assert any("Telnet" in t for t in titles)
    assert any("明文" in t for t in titles)
    assert any("FTP" in t or "HTTP" in t for t in titles)
    assert any("日志主机" in t for t in titles)
    assert any("NTP" in t for t in titles)
    assert any("SSH" in t for t in titles)
    assert r["counts"]["high"] >= 2 and r["score"] < 60 and r["grade"] in ("中", "差")


def test_checkup_clean_huawei_config():
    """合规配置：无高危命中，分数高。"""
    r = ops.checkup_config(HUAWEI_CONFIG_CLEAN, "huawei")
    assert r["counts"]["high"] == 0
    assert r["score"] >= 75
    # 命中的最多是低危（如 vty 有 acl 不命中；日志/NTP 已配）
    assert all(i["severity"] != "high" for i in r["items"])


def test_checkup_undo_line_not_flagged():
    """undo/no 关闭态不误报（telnet 已关闭不应告警 Telnet 开启）。"""
    cfg = H3C_CONFIG.replace("telnet server enable", "undo telnet server enable")
    r = ops.checkup_config(cfg, "h3c")
    assert not any("Telnet" in i["title"] for i in r["items"])


def test_checkup_vty_acl_detection():
    """vty 块含 acl 引用不命中；无 acl 引用命中。"""
    no_acl = "user-interface vty 0 4\n authentication-mode scheme\n#\n"
    assert ops._vty_block_without_acl(no_acl) is True
    with_acl = "user-interface vty 0 4\n acl 2001 inbound\n#\n"
    assert ops._vty_block_without_acl(with_acl) is False


# ---------- 备份：创建/列表/差异/无恢复红线 ----------

@pytest.fixture()
def nd():
    rec = db.save_netdev_device({"name": "备份测试设备", "vendor": "h3c", "host": "10.99.0.1",
                                 "port": 22, "username": "admin", "password": "x",
                                 "group_name": "备份测试组"})
    yield rec
    db.delete_netdev_device(rec["id"])
    for b in db.list_backups(rec["id"]):
        db.delete_backup(b["id"])


def test_backup_create_and_diff(nd, monkeypatch):
    """备份=拉配置全文存档（.conf + 快照 JSON）；两份备份可做文本 diff。"""
    configs = iter(["sysname GA-SW\n#version 7.1.070, Release 6351P06\nvlan 10\n"
                    "interface GigabitEthernet1/0/1\n port link-mode route\n",
                    "sysname GA-SW-NEW\n#version 7.1.070, Release 6351P06\nvlan 10\nvlan 20\n"
                    "interface GigabitEthernet1/0/1\n port link-mode route\n description uplink\n"])

    async def fake_run(device, commands, timeout=30, **kw):
        return {"ok": True, "output": f"<dev> display current-configuration\n{next(configs)}"}

    monkeypatch.setattr(ops.netdev_service, "run_commands", fake_run)
    a = asyncio_run(ops.create_backup(nd, "第一次备份"))
    b = asyncio_run(ops.create_backup(nd, "第二次备份"))
    assert a["id"].startswith("bk_") and b["id"].startswith("bk_")
    rows = db.list_backups(nd["id"])
    assert len(rows) == 2
    full = db.get_backup(rows[0]["id"])   # list 视图不含 snapshot_json，取完整记录
    snap = json.loads(full["snapshot_json"])
    assert snap["type"] == "netdev" and "sysname" in snap["config"]

    d = ops.diff_backups(a["id"], b["id"])
    assert d["added"] >= 1   # vlan 20 为新增行
    assert "+vlan 20" in d["diff"].splitlines() or any(l.startswith("+vlan 20") for l in d["diff"].splitlines())


def test_backup_diff_rejects_sangfor_snapshot(nd, monkeypatch):
    """非网络设备备份（深信服结构化快照）不参与文本 diff——类型守卫。"""
    rec = db.create_backup({"id": db.new_id("bk_"), "device_id": nd["id"], "label": "旧结构",
                            "kind": "manual", "sw_version": "", "snapshot_json": '{"meta": {}}',
                            "file_path": "", "file_sha256": "",
                            "created_at": db.now(), "created_by": "user"})
    configs = iter(["sysname X\n#version 7.1.070\nvlan 10\ninterface GigabitEthernet1/0/1\n port link-mode route\n description test\n"])

    async def fake_run(device, commands, timeout=30, **kw):
        return {"ok": True, "output": "display current-configuration\n" + next(configs)}

    monkeypatch.setattr(ops.netdev_service, "run_commands", fake_run)
    netdev_bk = asyncio_run(ops.create_backup(nd, "网络设备备份"))
    with pytest.raises(ValueError, match="不是网络设备配置备份"):
        ops.diff_backups(rec["id"], netdev_bk["id"])


def test_no_restore_capability_in_service():
    """红线守卫：netdev_ops_service 不得提供恢复/下发类函数。"""
    forbidden = ("restore", "apply", "rollback", "push", "deploy")
    funcs = [n for n in dir(ops) if callable(getattr(ops, n)) and not n.startswith("_")]
    for f in forbidden:
        assert not any(f in n.lower() for n in funcs), f"发现疑似恢复/下发能力: {f}"


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
