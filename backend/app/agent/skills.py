"""Agent 技能层：把工具按运维场景组织为技能（Skill），按需注入。

设计原则（与答辩口径一致）：
- 技能 = 触发描述 + 专属写工具集 + 流程指引；只读工具永远全量可用，技能只收窄"写工具"
  的作用域——跨技能查询不被卡死，写权限只收不扩；
- 关键词路由零延迟、离线可用；未命中时由 LLM 按技能目录兜底选择；仍无命中回退全量模式；
- 技能通过工具名反向引用，tools.py 无需感知技能的存在；
- 技能选择不消耗工具循环轮数上限（orchestrator 侧保证）。
"""
import re
from dataclasses import dataclass

from app.agent.tools import get_tools_by_name

KB_TOOL_NAME = "search_official_knowledge"
KB_RECORD_TOOL_NAME = "record_to_kb"
KB_INGEST_TOOL_NAME = "ingest_url_to_kb"


@dataclass
class Skill:
    id: str
    name: str
    description: str                      # 何时使用（给路由器/LLM 看）
    keywords: tuple[str, ...] = ()        # 关键词路由（子串匹配，离线可用）
    tools: tuple[str, ...] = ()           # 技能解锁的写工具（只读工具天然全量）
    guide: str = ""                       # 注入系统提示词的流程指引
    device_type: str | None = None        # None=通用，'af'/'ac'=仅该设备类型
    device_types: tuple[str, ...] = ()    # 适用设备类型（空=全部；netdev=网络设备 SSH）


SKILLS: list[Skill] = [
    Skill(
        id="netdev", name="网络设备运维",
        description="交换机/路由器（华为/H3C/锐捷）操作：配置查询、健康检查、路由/接口/ARP 表查询、"
                    "接口与路由配置下发、终端定位（IP/MAC→接入交换机+端口）、故障日志分析",
        keywords=("交换机", "路由器", "arp表", "arp 表", "查arp", "arp 查询", "终端定位", "定位终端",
                  "接入端口", "mac地址表", "mac 表", "logbuffer", "routing-table", "display ",
                  "锐捷", "h3c", "华为交换机", "系统视图", "保存配置", "网络设备",
                  "接口配置", "配置接口"),
        tools=("netdev_apply_config", "netdev_run_commands"),
        guide="网络设备运维技能：AI 对话仅支持华为/H3C/锐捷；查询用 netdev_get_status / "
              "netdev_get_config / netdev_get_interfaces / netdev_get_routes / netdev_get_arp / "
              "netdev_get_logs；终端定位用 netdev_locate_terminal（传 IP/MAC）；"
              "配置变更用 netdev_apply_config（自动进入系统/接口视图，默认不保存，需要持久化传 save=true），"
              "生成确认卡片后才能执行；特殊命令用 netdev_run_commands（原样下发）。"
              "多台设备用 devices 参数传设备名/IP 列表实现批量；非华为/H3C/锐捷设备提示到"
              "「网络设备管理」页操作。",
    ),
    Skill(
        id="status", name="状态巡检",
        description="查看设备运行状态、健康度、CPU/内存/磁盘、会话数、接口流量与安全区域",
        keywords=("运行状态", "健康", "cpu", "内存", "磁盘", "负载", "会话数", "运行时间",
                  "接口", "网口", "流量", "安全区域", "zone"),
        device_types=("af", "ac", "scp"),
        guide="状态巡检技能：先用 get_device_status 概览，再按需下钻 get_interfaces / get_zones；"
              "指标超阈值时主动建议 run_config_checkup 复核资源类风险。",
    ),
    Skill(
        id="config-query", name="配置查询",
        description="查询 NAT、访问控制、网络对象、自定义服务、路由、黑白名单、用户绑定等配置明细",
        keywords=("查看nat", "nat策略", "nat 规则", "访问控制", "acl", "策略列表", "网络对象", "地址组",
                  "自定义服务", "服务列表", "静态路由", "黑白名单", "黑名单", "白名单",
                  "绑定", "在线用户", "查一下", "看一眼", "列出"),
        device_types=("af", "ac", "scp"),
        guide="配置查询技能：只读查询，不做任何变更；结果较多时按关键词过滤（工具支持 keyword 参数）；"
              "AC 设备查绑定必须分别调用 get_user_bindings 与 get_ipmac_bindings 并分开展示。",
    ),
    Skill(
        id="checkup", name="配置体检与修复",
        description="运行配置合理性体检，识别规则冲突/空策略/过宽权限/高危端口等风险，并对风险项给出修复（停用/收紧/删除）",
        keywords=("体检", "检查配置", "风险", "扫描", "加固", "一键修复", "修复", "基线", "合规"),
        device_types=("af", "ac", "scp"),
        tools=("update_acl_rule", "update_nat_rule", "delete_acl_rule", "delete_nat_rule",
               "update_user_binding", "delete_user_binding",
               "update_network_object", "delete_network_object",
               "update_service", "delete_service",
               "update_whiteblacklist", "delete_whiteblacklist"),
        guide="配置体检与修复技能：先 run_config_checkup 出报告 → 向用户解读高风险项 → "
              "对确认要修复的项调用对应写工具生成变更计划（走确认卡片）；"
              "修复前建议 create_backup 留底；修复后再次 run_config_checkup 复核分数变化。",
    ),
    Skill(
        id="backup", name="备份与恢复",
        description="创建配置备份、查看备份列表、对比差异、按备份恢复配置",
        keywords=("备份", "恢复", "回滚", "回退", "快照", "差异", "对比", "diff"),
        device_types=("af", "ac", "scp"),
        tools=("restore_backup", "execute_restore"),
        guide="备份与恢复技能：create_backup 可直接执行；恢复必须先 restore_backup 生成恢复计划卡片，"
              "用户确认后才能 execute_restore；恢复前后用 diff_backups 展示变化。",
    ),
    Skill(
        id="device-access", name="设备接入",
        description="列出可用设备（深信服+网络设备）、通过对话添加新的深信服设备",
        keywords=("添加设备", "接入设备", "新设备", "设备列表", "有哪些设备", "切换设备", "纳管"),
        tools=("add_device",),
        guide="设备接入技能：list_available_devices 列出已有设备（含网络设备）；添加深信服设备用 "
              "add_device 生成确认卡片（会先测试连接）；引导用户提供名称、类型、地址与凭据；"
              "网络设备（交换机/路由器）请在「网络设备管理」页面添加。",
    ),
    Skill(
        id="policy-change", name="策略变更",
        description="新建/修改/删除 NAT、访问控制、用户绑定、网络对象、服务、黑白名单等配置变更",
        keywords=("新建", "创建", "添加", "新增", "停用", "启用", "禁用", "修改", "删除", "变更",
                  "放行", "封禁", "阻断", "收紧", "加一条", "删掉"),
        device_types=("af", "ac", "scp"),
        tools=("create_nat_rule", "update_nat_rule", "delete_nat_rule",
               "create_acl_rule", "update_acl_rule", "delete_acl_rule",
               "create_user_binding", "update_user_binding", "delete_user_binding",
               "create_network_object", "update_network_object", "delete_network_object",
               "create_service", "update_service", "delete_service",
               "create_whiteblacklist", "update_whiteblacklist", "delete_whiteblacklist"),
        guide="策略变更技能：所有变更先定位目标（只读查询核实 ID 与现状），再调用写工具生成变更计划卡片；"
              "高危项（删除、高危端口、any 放行）会触发二次确认；变更前建议 create_backup。"
              "多台设备同一变更可用 devices 参数批量下发（生成统一确认卡片）。",
    ),
    Skill(
        id="upgrade", name="软件升级建议",
        description="获取官方软件更新信息、安全公告命中、升级路径与升级时机建议",
        keywords=("升级", "更新", "新版本", "版本建议", "psirt", "漏洞公告", "eol", "停产"),
        device_types=("af", "ac", "scp"),
        guide="软件升级建议技能：升级类问题必须先调 get_software_updates / get_upgrade_advice 取数；"
              "明确提示升级会导致业务中断、建议低峰窗口；Agent 只给建议与行动清单，不代执行升级。",
    ),
]


# 知识问答意图：设备功能作用类 / 故障排查类（未勾选知识库时自动启用本地优先检索）
KB_INTENT_PATTERN = re.compile(
    r"故障|报错|错误|异常|告警|不生效|不通|断网|丢包|无法|失败|排查|原因|为什么|为何|解决|处理|恢复|"
    "冲突|占满|虚高|抖动|频繁|重启后|掉线|变慢|超时|丢包率|"
    "作用|用途|是什么|什么是|怎么用|怎么配|如何配|如何使用|怎么解决|如何解决|区别|原理|场景|功能介绍|意义|好处|优缺点|"
    "支持哪些|支持什么|哪些版本|哪个版本|版本特性|特性|新版本功能|新功能|新增功能|功能列表|"
    "怎么配置|如何配置|配置方法|配置教程|用法|使用方法|步骤|教程|最好|最佳|建议.*(?:配置|设置|方案)|"
    "注意事项|前提条件|前置条件|依赖|兼容",
    re.I)


def is_kb_intent(message: str) -> bool:
    """判断是否为知识问答类提问（功能作用/故障排查），未勾选知识库时也自动启用分层检索。"""
    if not message:
        return False
    return bool(KB_INTENT_PATTERN.search(message))


# 写操作意图词：技能 LLM 兜底路由仅对疑似写操作的消息触发（收窄写工具集 + 注入技能指引）；
# 纯查询/问答直接走全量工具模式，省去一次串行前置 LLM 调用（关键词路由未命中时）。
# 误伤方向安全：漏判 → 全量模式（现状兜底行为）；误判 → 仅多一次快速路由调用。
WRITE_INTENT_PATTERN = re.compile(
    r"新建|创建|添加|新增|加一条|加个|删除|删掉|删了|去掉|移除|清除|清空|"
    r"修改|改成|改为|改一下|改下|编辑|更新|调整|停用|启用|禁用|开启|关闭|"
    r"放行|封禁|阻断|封锁|收紧|放开|还原|恢复|回滚|回退|重置|变更|下发|执行|保存配置",
    re.I)


def looks_like_write_intent(message: str) -> bool:
    """疑似配置写操作提问：命中才值得做 LLM 技能兜底路由。"""
    if not message:
        return False
    return bool(WRITE_INTENT_PATTERN.search(message))


def _skills_for(device_type: str) -> list[Skill]:
    """按设备上下文类型过滤技能：device_types 为空=通用；否则仅列出的类型可用。
    全局模式（global）不限制——全部技能可见。"""
    if device_type == "global":
        return list(SKILLS)
    return [s for s in SKILLS if not s.device_types or device_type in s.device_types]


def select_skill(message: str, device_type: str = "") -> Skill | None:
    """关键词路由：命中即返回，零延迟、离线可用；未命中返回 None。"""
    text = (message or "").lower()
    if not text:
        return None
    for skill in _skills_for(device_type):
        for kw in skill.keywords:
            if kw in text:
                return skill
    return None


def skill_catalog_message(device_type: str = "") -> str:
    """构建技能目录（供 LLM 兜底选择）。"""
    lines = [
        "你是技能路由器。根据用户消息从下列技能中选择最合适的一个，只输出技能编号（如 skill-2）；"
        "都不匹配只输出 NONE。不要输出任何其他内容。",
        "## 技能目录",
    ]
    for i, s in enumerate(_skills_for(device_type), 1):
        lines.append(f"[skill-{i}] {s.name}：{s.description}")
    return "\n".join(lines)


def parse_skill_choice(text: str, device_type: str = "") -> Skill | None:
    """解析 LLM 的技能选择（skill-N 编号或技能名）。"""
    if not text:
        return None
    available = _skills_for(device_type)
    m = re.search(r"skill-(\d+)", text)
    if m and 1 <= int(m.group(1)) <= len(available):
        return available[int(m.group(1)) - 1]
    lowered = text.strip().lower()
    for s in available:
        if s.name in text or s.id in lowered:
            return s
    return None


def resolve_skill_tools(skill: Skill | None, device_type: str = "") -> list:
    """技能工具集 = 全部只读工具 + 技能解锁的写工具 + 知识库沉淀登记工具（按设备类型过滤）。

    只读工具与 record_to_kb（用户明确要求沉淀时的入口，走确认卡片）永远可用：
    跨技能查询不被卡死；写权限只收不扩。
    """
    available = get_tools_by_name(device_type)
    if skill is None:
        return list(available.values())
    unlocked = set(skill.tools) | {KB_RECORD_TOOL_NAME, KB_INGEST_TOOL_NAME}
    return [t for name, t in available.items() if not t.write or name in unlocked]


def skill_guide_message(skill: Skill) -> str:
    return f"## 当前技能：{skill.name}\n{skill.guide}"


def kb_auto_guide() -> str:
    """未勾选知识库时的自动检索指引：本地个人知识库优先，官方知识库兜底。"""
    lines = [
        "## 知识问答技能（本地优先，自动启用）",
        "该问题属于设备功能/故障排查类，请按以下顺序检索后作答：",
        "1. 先调用 search_personal_kb 检索本地个人知识库（keyword 传 2~3 个空格分隔的核心词，如 'HA 主备'，不要传整句）；",
        "2. 本地命中（matches>0）：基于本地词条作答，并向用户注明出自个人知识库；"
        "本地信息不足以完整回答时，再调用 search_official_knowledge 查询官方知识库补充；",
        "3. 本地未命中：调用 search_official_knowledge 查询官方知识库，回答末尾附官方引用"
        "（带 url 的引用以 [标题](url) 呈现）；官方也未命中则如实说明，不要编造；",
        "4. 设备实时数据（状态/策略/资源）仍以设备工具为准。此模式不自动沉淀个人知识库。",
    ]
    return chr(10).join(lines)


def kb_search_skill() -> Skill:
    """知识库检索技能：勾选「查询知识库」后由 orchestrator 条件启用（不参与关键词路由）。"""
    return Skill(
        id="kb-search", name="知识库检索",
        description="检索深信服官方知识库（诸葛小T），回答产品配置方法、故障排查、版本兼容等通用技术问题",
        tools=(KB_TOOL_NAME,),
        guide="## 知识库检索技能（已启用）\n"
              "- 涉及深信服产品的配置方法、故障排查思路、版本兼容、官方最佳实践类问题，"
              "先调用 search_official_knowledge 检索官方知识库，再基于返回内容作答；\n"
              "- 回答末尾以「官方参考」列出引用，**带 url 的引用必须以 markdown 链接 [标题](url) 原样呈现**，"
              "不得改写或丢弃链接；知识库未命中或服务不可用时如实说明，不要编造知识库内容；\n"
              "- 设备实时数据（状态/策略列表/资源占用）仍以设备工具为准，不要用知识库内容替代。",
    )
