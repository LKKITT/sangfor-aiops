"""升级建议引擎与版本知识库测试。"""
import pytest

from app.services.knowledge import versions as kb
from app.services.upgrade_advisor import build_upgrade_advice


def test_normalize_version():
    assert kb.normalize_version("AF 8.0.85") == "8.0.85"
    assert kb.normalize_version("AC&SG 13.0.121") == "13.0.121"
    assert kb.normalize_version("8.0.107") == "8.0.107"


def test_version_key_ordering():
    assert kb.version_key("8.0.9") < kb.version_key("8.0.85")
    assert kb.version_key("8.0.107") > kb.version_key("8.0.106")
    assert kb.version_key("13.0.9") < kb.version_key("13.0.121")


def test_upgrade_path_new_arch():
    path = kb.upgrade_path("af", "8.0.85", "8.0.107")
    assert path["hops"] == ["8.0.106", "8.0.107"]   # 8.0.48 为旧架构末端，新架构自 8.0.69 起
    assert not path["cross_arch_migration"]


def test_upgrade_path_old_arch_requires_migration():
    path = kb.upgrade_path("af", "8.0.32", "8.0.107")
    assert path["cross_arch_migration"] is True
    # 旧链走完到末端 8.0.48，跨架构后接新架构链：… → 8.0.69 → … → 8.0.107
    assert path["hops"][:3] == ["8.0.35", "8.0.45", "8.0.48"]
    assert path["hops"][-1] == "8.0.107"
    assert any("重装系统盘" in n for n in path["notes"])


def test_upgrade_path_old_arch_tail_upgrades_to_new_chain():
    """8.0.45 先升到旧架构末端 8.0.48，跨架构需客服评估 + 重装系统盘后接新架构链。"""
    path = kb.upgrade_path("af", "8.0.45", "8.0.107")
    assert path["cross_arch_migration"] is True
    assert path["hops"][0] == "8.0.48"               # 旧架构末端
    assert path["hops"][1] == "8.0.69"               # 跨架构后的新架构首版
    assert path["hops"][-1] == "8.0.107"
    assert any("客服" in n and "重装系统盘" in n for n in path["notes"])


def test_upgrade_path_old_arch_end_cross():
    """8.0.48（旧架构末端）跨架构升级提示客服评估 + 重装系统盘。"""
    path = kb.upgrade_path("af", "8.0.48", "8.0.107")
    assert path["cross_arch_migration"] is True
    assert path["hops"][0] == "8.0.69"
    assert any("重装系统盘" in n for n in path["notes"])


def test_upgrade_path_ac_direct_to_stable():
    """AC 12.0.40 直达稳定版 13.0.121（需前置检测）。"""
    path = kb.upgrade_path("ac", "12.0.40")
    assert path["hops"] == ["13.0.121"]
    assert path["direct_upgrade"] is True
    assert any("前置检测" in n for n in path["notes"])


def test_upgrade_path_up_to_date():
    path = kb.upgrade_path("af", "8.0.107", "8.0.107")
    assert path["hops"] == []
    assert any("最新" in n for n in path["notes"])


def test_psirt_hit():
    hits = kb.advisories_for("ac", "13.0.53")
    assert len(hits) == 1
    assert hits[0]["id"] == "SF-PSIRT-20220472"
    assert hits[0]["cvss"] == 9.8
    assert kb.advisories_for("ac", "13.0.121") == []


def test_eol_detection():
    eol, detail = kb.is_eol("ac", "12.0.80")
    assert eol and "不再获得" in detail
    assert not kb.is_eol("af", "8.0.107")[0]


def test_releases_between_contains_notes():
    releases = kb.releases_between("af", "8.0.85", "8.0.107")
    assert [r.version for r in releases] == ["8.0.106", "8.0.107"]
    notes_107 = [n for n in releases[-1].notes]
    assert any("mbuf" in n["title"] for n in notes_107)


@pytest.mark.asyncio
async def test_advice_known_issue_triggers():
    advice = await build_upgrade_advice("AF 8.0.85", {"mbuf_usage": 76}, "t")
    assert advice["risk"] in ("medium", "high")
    assert any(r["type"] == "已知问题" for r in advice["reasons"])
    assert advice["upgrade_path"]["hops"] == ["8.0.106", "8.0.107"]
    assert advice["checklist"][0]["agent_action"] == "backup_now"
    assert "低峰" in advice["timing"]["window"]


@pytest.mark.asyncio
async def test_advice_cross_arch_checklist():
    """AF 8.0.45 行动清单：旧架构升级、客服评估+重装系统盘、新架构升级分段呈现。"""
    advice = await build_upgrade_advice("AF 8.0.45", {}, "t")
    steps = [s["action"] for s in advice["checklist"]]
    assert any(s.startswith("旧架构内逐级升级：8.0.48") for s in steps)
    assert any("客服评估" in s and "重装系统盘" in s for s in steps)
    assert any(s.startswith("迁移完成后再逐级升级新架构：8.0.69") for s in steps)


@pytest.mark.asyncio
async def test_advice_psirt_high_risk():
    advice = await build_upgrade_advice("AC 13.0.53", {}, "t")
    assert advice["risk"] == "high"
    assert advice["recommendation"] == "强烈建议尽快升级"
    assert any(r["type"] == "安全漏洞" for r in advice["reasons"])


@pytest.mark.asyncio
async def test_advice_up_to_date():
    advice = await build_upgrade_advice("AF 8.0.107", {}, "t")
    assert advice["up_to_date"] is True
    assert any(r["level"] == "info" for r in advice["reasons"])
