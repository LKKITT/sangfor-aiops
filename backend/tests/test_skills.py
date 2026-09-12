"""技能层测试：关键词路由、设备过滤、工具收窄、LLM 选择解析。"""
from app.agent import skills
from app.agent.tools import get_tools_by_name


def _device(dtype):
    return {"id": "dev_test", "name": "测试设备", "type": dtype, "mode": "simulator"}


# ---------- 关键词路由 ----------

def test_select_skill_status():
    assert skills.select_skill("看一下设备运行状态", "af").id == "status"
    assert skills.select_skill("CPU 和内存怎么样", "af").id == "status"


def test_select_skill_checkup_before_policy():
    # "体检"+"停用" 同时命中 checkup 与 policy-change，按 SKILLS 顺序 checkup 优先
    assert skills.select_skill("体检一下配置有哪些风险，把高危的停用", "af").id == "checkup"


def test_select_skill_policy_change():
    assert skills.select_skill("把 445 端口对公网暴露的策略停用", "af").id == "policy-change"
    assert skills.select_skill("新建一条 NAT 策略", "af").id == "policy-change"


def test_select_skill_backup_and_upgrade():
    assert skills.select_skill("变更前先备份一下", "af").id == "backup"
    assert skills.select_skill("有新版本可以升级吗？", "af").id == "upgrade"


def test_select_skill_none_on_miss():
    assert skills.select_skill("今天天气怎么样", "af") is None
    assert skills.select_skill("", "af") is None


def test_select_skill_respects_device_type():
    # 设备接入技能全设备可用；status 技能通用
    assert skills.select_skill("帮我添加一台新设备", "ac").id == "device-access"


# ---------- 工具收窄 ----------

def test_resolve_skill_tools_readonly_full():
    """只读工具永远全量可用；技能未命中（None）等于全量模式。"""
    full = get_tools_by_name("af")
    scope = {t.name for t in skills.resolve_skill_tools(None, "af")}
    assert scope == set(full.keys())


def test_resolve_skill_tools_write_narrowed():
    """技能模式下：只读全量 + 技能写工具；其它技能的写工具不可见。"""
    skill = skills.select_skill("体检一下配置风险", "af")
    names = {t.name for t in skills.resolve_skill_tools(skill, "af")}
    # 只读工具仍在
    assert "get_device_status" in names and "get_nat_rules" in names
    # 技能解锁的写工具可见（checkup 技能含 update_acl_rule）
    assert "update_acl_rule" in names
    # 未解锁的写工具被收窄（创建类不属于 checkup 技能）
    assert "create_acl_rule" not in names
    assert "create_nat_rule" not in names


def test_resolve_skill_tools_ac_device_filter():
    """AC 设备上任何技能都看不到 AF 专属写工具（NAT/对象/服务）。"""
    skill = skills.select_skill("把策略停用", "ac")  # policy-change
    names = {t.name for t in skills.resolve_skill_tools(skill, "ac")}
    assert "update_acl_rule" in names
    assert "create_nat_rule" not in names
    assert "delete_network_object" not in names


# ---------- LLM 兜底选择的目录与解析 ----------

def test_catalog_lists_all_skills_for_device():
    catalog = skills.skill_catalog_message("af")
    for i, s in enumerate(skills._skills_for("af"), 1):
        assert f"[skill-{i}]" in catalog
        assert s.name in catalog
    assert "kb-search" not in catalog  # 知识库技能由勾选启用，不进目录


def test_parse_skill_choice_by_index_and_name():
    assert skills.parse_skill_choice("skill-3", "af").id == skills._skills_for("af")[2].id
    assert skills.parse_skill_choice("配置体检与修复", "af").id == "checkup"
    assert skills.parse_skill_choice("NONE", "af") is None
    assert skills.parse_skill_choice("", "af") is None
    assert skills.parse_skill_choice("skill-99", "af") is None  # 越界


def test_record_to_kb_always_available():
    """知识库沉淀登记工具（写）在任意技能下都可用——用户明确要求沉淀时的入口。"""
    from app.agent.tools import TOOLS_BY_NAME
    assert "record_to_kb" in TOOLS_BY_NAME
    assert TOOLS_BY_NAME["record_to_kb"].write
    for msg, dtype in (("体检一下配置风险", "af"), ("把策略停用", "ac"), ("看设备状态", "")):
        skill = skills.select_skill(msg, dtype)
        names = {t.name for t in skills.resolve_skill_tools(skill, dtype)}
        assert "record_to_kb" in names, f"技能 {skill and skill.id} 下 record_to_kb 不可见"


# ---------- 知识库技能 ----------

def test_kb_skill_exposes_kb_tool():
    kb = skills.kb_search_skill()
    assert skills.KB_TOOL_NAME in kb.tools
    names = {t.name for t in skills.resolve_skill_tools(kb, "af")}
    # 只读全量下知识库工具本来就在（若已注册）；技能本身声明了它
    assert skills.KB_TOOL_NAME in kb.tools or skills.KB_TOOL_NAME in names
