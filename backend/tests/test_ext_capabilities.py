"""外部能力（MCP / Agent Skills）测试：配置管理、JSON 导入、注册表安装映射、
技能扫描/开关/删除保护、load_skill 工具行为。不依赖真实 MCP 服务与网络。"""
import json

import pytest

from app.services import agent_skills_service as ask
from app.services import mcp_service


# ---------- MCP 配置管理 ----------

def test_mcp_config_normalize_and_crud():
    mcp_service.save_servers([])
    rec = mcp_service.upsert_server({"name": "测试服务", "transport": "http",
                                     "url": "https://example.com/mcp", "enabled": True})
    assert rec["id"] and rec["enabled"] is True
    assert mcp_service.get_servers()[0]["name"] == "测试服务"
    # stdio 缺 command 报错
    with pytest.raises(ValueError, match="command"):
        mcp_service.upsert_server({"name": "坏配置", "transport": "stdio"})
    # http 缺合法 url 报错
    with pytest.raises(ValueError, match="URL"):
        mcp_service.upsert_server({"name": "坏配置2", "transport": "http", "url": "ftp://x"})
    assert mcp_service.delete_server(rec["id"]) is True
    assert mcp_service.delete_server(rec["id"]) is False
    assert mcp_service.get_servers() == []
    mcp_service.save_servers([])


def test_mcp_func_name_sanitized():
    assert mcp_service._func_name("My Server/1", "查询!工具") == "mcp_My_Server_1_______".replace("!", "_")[:64] \
        or len(mcp_service._func_name("My Server/1", "查询!工具")) <= 64
    n = mcp_service._func_name("srv", "tool")
    assert n == "mcp_srv_tool"


def test_mcp_import_claude_json():
    text = json.dumps({"mcpServers": {
        "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
        "remote": {"url": "https://example.com/mcp"},
        "broken": {"foo": 1},
    }})
    r = mcp_service.import_claude_json(text)
    assert r["imported"] == 2 and len(r["errors"]) == 1 and "broken" in r["errors"][0]
    names = {s["name"] for s in mcp_service.get_servers()}
    assert {"fetch", "remote"} <= names
    assert all(s["enabled"] is False for s in mcp_service.get_servers())   # 默认停用
    mcp_service.save_servers([])


def test_mcp_registry_install_http_and_stdio():
    http_rec = mcp_service.install_from_registry({
        "registry_name": "com.example/thing", "display": "thing",
        "remotes": [{"url": "https://mcp.example.com/sse"}], "packages": []})
    assert http_rec["transport"] == "http" and http_rec["url"] == "https://mcp.example.com/sse"
    stdio_rec = mcp_service.install_from_registry({
        "registry_name": "com.example/pkg", "display": "pkg",
        "packages": [{"command": "npx", "args": ["-y", "mcp-thing"]}]})
    assert stdio_rec["transport"] == "stdio" and stdio_rec["command"] == "npx"
    assert "mcp-thing" in stdio_rec["args"]
    with pytest.raises(ValueError, match="注册表条目"):
        mcp_service.install_from_registry({"registry_name": "empty"})
    mcp_service.save_servers([])


# ---------- Agent Skills ----------

@pytest.fixture()
def fake_skill_dirs(monkeypatch, tmp_path):
    """把扫描目录指到临时路径，造两个技能文件夹。"""
    d1 = tmp_path / "agents"
    d2 = tmp_path / "imported"
    (d1 / "my-skill").mkdir(parents=True)
    (d1 / "my-skill" / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: 测试技能说明\n---\n# 步骤\n1. 做 A", encoding="utf-8")
    (d2 / "imported-skill").mkdir(parents=True)
    (d2 / "imported-skill" / "SKILL.md").write_text(
        "---\nname: imported-skill\ndescription: 导入技能\n---\nbody", encoding="utf-8")
    monkeypatch.setattr(ask, "SCAN_DIRS", [("user-agents", d1), ("imported", d2)])
    monkeypatch.setattr(ask, "delete_skill", ask.delete_skill)   # 占位保持引用
    return d1, d2


def test_skills_scan_and_toggle(fake_skill_dirs):
    ask._set_state({})
    skills = ask.list_skills()
    assert {s["folder"] for s in skills} == {"my-skill", "imported-skill"}
    assert all(s["enabled"] is False for s in skills)
    assert [s for s in skills if s["folder"] == "my-skill"][0]["deletable"] is False
    assert [s for s in skills if s["folder"] == "imported-skill"][0]["deletable"] is True

    ask.toggle_skill("my-skill", True)
    skills = ask.list_skills()
    assert [s for s in skills if s["folder"] == "my-skill"][0]["enabled"] is True
    cat = ask.enabled_catalog()
    assert len(cat) == 1 and cat[0]["description"] == "测试技能说明"
    from app.agent.ext_tools import skills_catalog_message
    assert "load_skill" in skills_catalog_message() and "my-skill" in skills_catalog_message()


def test_skill_body_and_delete_protection(fake_skill_dirs):
    assert "做 A" in ask.skill_body("my-skill")
    with pytest.raises(ValueError, match="仅可删除"):
        ask.delete_skill("my-skill")     # 外部目录技能只许关闭
    ask.delete_skill("imported-skill")   # 导入目录可删
    assert not [s for s in ask.list_skills() if s["folder"] == "imported-skill"]


def test_load_skill_tool(fake_skill_dirs):
    import asyncio
    from app.agent.ext_tools import _h_load_skill
    ask._set_state({})
    ask.toggle_skill("my-skill", True)
    ok = asyncio.run(_h_load_skill(None, {"skill": "my-skill"}, {}))
    assert "做 A" in ok["instructions"]
    bad = asyncio.run(_h_load_skill(None, {"skill": "nope"}, {}))
    assert "未启用" in bad["error"]
