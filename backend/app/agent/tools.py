"""Agent 工具集：定义 OpenAI function-calling 工具协议与执行器。

工具分两类：
- read：直接执行，结果回填给 LLM 继续推理；
- write：先经 guardrails 检查 → prepare() 生成人类可读变更计划 → 存 pending_action
  → 前端确认卡片 → 用户确认后由 orchestrator 调 execute() 真正下发。
"""
import json
from typing import Any, Awaitable, Callable

from app import db
from app.adapters.base import ChangeOp, DeviceClient
from app.services import config_service
from app.services import update_service, upgrade_advisor
from app.services.analyzer import run_checks
from app.agent import guardrails

ToolHandler = Callable[[DeviceClient, dict, dict], Awaitable[Any]]


def _rule_brief(r: dict) -> str:
    return (f"{r.get('name', r.get('id'))} [{r.get('id')}] {r.get('src_zone', 'any')}:{r.get('src_addr', 'any')}"
            f" → {r.get('dst_zone', 'any')}:{r.get('dst_addr', 'any')} 服务={r.get('service', 'any')}"
            f" 动作={r.get('action', '')} 启用={r.get('enabled', True)}")


class Tool:
    def __init__(self, name: str, description: str, parameters: dict, handler: ToolHandler,
                 write: bool = False, prepare: ToolHandler | None = None):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.write = write
        self.prepare = prepare
        guardrails.register_meta(name, {"write": write})

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


# ============================ 查询类工具 ============================

async def _h_status(client: DeviceClient, args: dict, device: dict) -> dict:
    s = await client.get_status()
    d = s.to_dict()
    d["summary"] = (f"版本 {d['sw_version']}，CPU {d['cpu_usage']}%，内存 {d['memory_usage']}%，"
                    f"磁盘 {d['disk_usage']}%，mbuf {d['mbuf_usage']}%，会话 {d['session_count']}")
    return d


async def _h_interfaces(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return [i.to_dict() for i in await client.get_interfaces()]


async def _h_nat(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    rules = [n.to_dict() for n in await client.get_nat_rules()]
    kw = str(args.get("keyword", "")).strip().lower()
    if kw:
        rules = [r for r in rules if kw in json.dumps(r, ensure_ascii=False).lower()]
    return rules


async def _h_acl(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    rules = [a.to_dict() for a in await client.get_acl_rules()]
    kw = str(args.get("keyword", "")).strip().lower()
    if kw:
        rules = [r for r in rules if kw in json.dumps(r, ensure_ascii=False).lower()]
    return rules


async def _h_bindings(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return [b.to_dict() for b in await client.get_user_bindings()]


async def _h_routes(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return [r.to_dict() for r in await client.get_static_routes()]


async def _h_checkup(client: DeviceClient, args: dict, device: dict) -> dict:
    snapshot = await client.snapshot_config()
    status = (await client.get_status()).to_dict()
    report = run_checks(snapshot, status)
    report["summary_text"] = (
        f"体检得分 {report['score']}/100（{report['grade']}）：高危 {report['counts']['high']} 项、"
        f"中危 {report['counts']['medium']} 项、低危 {report['counts']['low']} 项，"
        f"其中 {report['auto_fixable']} 项支持一键修复")
    return report


async def _h_create_backup(client: DeviceClient, args: dict, device: dict) -> dict:
    rec = await config_service.create_backup(device["id"], label=str(args.get("label", "") or ""),
                                             kind="manual", created_by="agent")
    return {"backup_id": rec["id"], "label": rec["label"], "created_at": rec["created_at"],
            "sw_version": rec["sw_version"], "message": "备份完成（结构化快照 + 配置文件归档）"}


async def _h_list_backups(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return db.list_backups(device["id"])[:20]


async def _h_diff(client: DeviceClient, args: dict, device: dict) -> dict:
    result = config_service.diff_backups(str(args["backup_a_id"]), str(args["backup_b_id"]))
    d = result["diff"]
    return {"base": result["base"], "target": result["target"], "summary": d["summary"],
            "sections": d["sections"]}


async def _h_updates(client: DeviceClient, args: dict, device: dict) -> dict:
    status = await client.get_status()
    overview = await update_service.get_update_overview(status.sw_version)
    releases = [{"版本": r["version"],
                 "要点": [f"{n.get('category')}｜{n.get('title')}：{n.get('detail')}" for n in r["notes"]]}
                for r in overview["releases"]]
    return {"当前版本": overview["current_version"], "最新版本": overview["latest_version"],
            "已是最新": overview["up_to_date"],
            "跨越版本发布说明": releases,
            "命中的安全公告": overview["advisories_hit"], "EOL": overview["eol"],
            "数据来源": overview["sources"]}


async def _h_upgrade_advice(client: DeviceClient, args: dict, device: dict) -> dict:
    status = (await client.get_status()).to_dict()
    advice = await upgrade_advisor.build_upgrade_advice(status["sw_version"], status, device["name"])
    advice["_llm_summary"] = (
        f"当前 {advice['current_version']} → 最新 {advice['latest_version']}；"
        f"建议：{advice['recommendation']}；路径：{' → '.join(advice['upgrade_path']['hops']) or '无需升级'}"
        f"{'；注意：' + '；'.join(advice['upgrade_path']['notes']) if advice['upgrade_path']['notes'] else ''}")
    return advice


async def _h_audit(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return db.list_audit(limit=int(args.get("limit", 20)))


# ============================ 变更类工具 ============================

NAT_FIELDS = ("type", "src_zone", "dst_zone", "src_addr", "dst_addr", "service",
              "translated_addr", "translated_port", "name", "comment", "log", "enabled")
ACL_FIELDS = ("src_zone", "dst_zone", "src_addr", "dst_addr", "service", "app",
              "action", "name", "comment", "log", "enabled")
BIND_FIELDS = ("user", "ip", "mac", "binding_type", "comment", "enabled")


async def _prepare_rule_change(client: DeviceClient, args: dict, device: dict) -> dict:
    """通用变更计划生成：返回含变更前后对照的确认卡片数据。"""
    resource = args["_resource"]
    op = args["_op"]
    section = {"nat": "nat_rules", "acl": "acl_rules", "binding": "user_bindings"}[resource]
    resource_cn = {"nat": "NAT 策略", "acl": "访问控制策略", "binding": "用户绑定"}[resource]
    rules = await client.snapshot_config()
    before = None
    target_id = str(args.get("rule_id", ""))
    for r in rules.get(section) or []:
        if str(r.get("id")) == target_id:
            before = r
            break
    data = {k: v for k, v in (args.get("data") or {}).items() if k in
            (NAT_FIELDS if resource == "nat" else ACL_FIELDS if resource == "acl" else BIND_FIELDS)}
    after = dict(before or {})
    after.update(data)
    title = {"create": f"新建{resource_cn}", "update": f"修改{resource_cn}「{before.get('name', target_id) if before else target_id}」",
             "delete": f"删除{resource_cn}「{before.get('name', target_id) if before else target_id}」"}[op]
    return {
        "title": title,
        "resource": resource, "resource_cn": resource_cn, "op": op, "target_id": target_id,
        "before": before, "after": after if op != "delete" else None,
        "fields": sorted(data.keys()),
        "warning": _change_warning(resource, op, before, after),
    }


def _change_warning(resource: str, op: str, before: dict | None, after: dict | None) -> str:
    if op == "delete":
        return "删除操作即时生效且影响该策略覆盖的全部流量，请确认业务已迁移"
    if resource == "acl" and after and after.get("action") == "allow" and \
            str(after.get("service", "")).lower() in ("any", ""):
        return "注意：该策略放行全部服务，请确认符合最小权限原则"
    if resource == "nat" and op == "update" and before and before.get("type") == "DNAT":
        return "注意：修改 DNAT 映射会影响对外发布的业务可达性"
    return ""


async def _h_rule_change(client: DeviceClient, args: dict, device: dict) -> dict:
    resource = args["_resource"]
    op = args["_op"]
    target_id = str(args.get("rule_id", ""))
    data = {k: v for k, v in (args.get("data") or {}).items()}
    change = ChangeOp(op=op, resource=resource, target_id=target_id, data=data)
    result = await client.apply_change(change)
    db.audit(f"agent.write.{resource}.{op}", {"target_id": target_id, "data": data},
             device_id=device["id"], result="ok")
    return result


async def _h_restore(client: DeviceClient, args: dict, device: dict) -> dict:
    backup_id = str(args["backup_id"])
    preview = await config_service.restore_preview(device["id"], backup_id)
    backup = db.get_backup(backup_id)
    return {
        "title": f"恢复配置到备份「{preview.get('label', backup_id)}」",
        "backup_id": backup_id,
        "backup_created_at": (backup or {}).get("created_at", ""),
        "plan": preview,
        "warning": "恢复将把设备关键配置回退到该备份状态；执行前系统会自动生成安全备份，可随时再次恢复",
    }


async def _h_restore_exec(client: DeviceClient, args: dict, device: dict) -> dict:
    return await config_service.restore_apply(device["id"], str(args["backup_id"]), operator="agent")


# ---- 工具注册 ----

def _write_tool(name: str, desc: str, params: dict, handler: ToolHandler,
                prepare: ToolHandler | None = None) -> Tool:
    return Tool(name, desc, params, handler, write=True, prepare=prepare)


TOOLS: list[Tool] = [
    Tool("get_device_status", "获取设备运行状态：版本、型号、CPU/内存/磁盘/mbuf 使用率、会话数、运行时间", {"type": "object", "properties": {}}, _h_status),
    Tool("get_interfaces", "获取网络接口列表：名称、区域、IP、状态、速率、收发流量", {"type": "object", "properties": {}}, _h_interfaces),
    Tool("get_nat_rules", "获取 NAT 策略列表（SNAT/DNAT），可按关键词过滤", {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "过滤关键词，如网段/端口/名称"}},
    }, _h_nat),
    Tool("get_acl_rules", "获取访问控制（应用控制）策略列表，可按关键词过滤", {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "过滤关键词"}},
    }, _h_acl),
    Tool("get_user_bindings", "获取 IP-MAC 用户绑定列表", {"type": "object", "properties": {}}, _h_bindings),
    Tool("get_static_routes", "获取静态路由列表", {"type": "object", "properties": {}}, _h_routes),
    Tool("run_config_checkup", "运行配置合理性体检：规则冲突/空策略/过宽权限/资源异常，返回风险清单与修复建议",
         {"type": "object", "properties": {}}, _h_checkup),
    Tool("create_backup", "立即创建一次设备配置备份（结构化快照+配置文件）", {
        "type": "object",
        "properties": {"label": {"type": "string", "description": "备份标签，如：变更前备份"}},
    }, _h_create_backup),
    Tool("list_backups", "列出该设备最近的配置备份", {"type": "object", "properties": {}}, _h_list_backups),
    Tool("diff_backups", "对比两份备份的配置差异", {
        "type": "object",
        "properties": {"backup_a_id": {"type": "string"}, "backup_b_id": {"type": "string"}},
        "required": ["backup_a_id", "backup_b_id"],
    }, _h_diff),
    _write_tool("restore_backup", "生成「恢复配置到指定备份」的变更计划（展示将删除/修改/重建的策略清单），用户确认后执行恢复", {
        "type": "object",
        "properties": {"backup_id": {"type": "string"}},
        "required": ["backup_id"],
    }, _h_restore, prepare=_h_restore),
    _write_tool("execute_restore", "真正执行配置恢复（仅在用户对恢复计划确认后调用）", {
        "type": "object",
        "properties": {"backup_id": {"type": "string"}},
        "required": ["backup_id"],
    }, _h_restore_exec),
    Tool("get_software_updates", "获取深信服官方软件更新信息：当前版本之后的版本发布说明（新增功能/安全修复/已知问题修复/优化）与命中安全公告",
         {"type": "object", "properties": {}}, _h_updates),
    Tool("get_upgrade_advice", "获取完整升级建议：是否升级、升级路径、关键变更、升级时机、行动清单",
         {"type": "object", "properties": {}}, _h_upgrade_advice),
    Tool("get_audit_logs", "查询最近的操作审计日志", {
        "type": "object",
        "properties": {"limit": {"type": "integer", "default": 20}},
    }, _h_audit),
]


def _register_write_tools() -> None:
    common_id = {"rule_id": {"type": "string", "description": "策略/记录 ID"}}

    def rule_tool(name: str, resource: str, op: str, desc: str, fields: tuple) -> Tool:
        props: dict[str, Any] = dict(common_id)
        props["data"] = {"type": "object",
                         "properties": {f: {"type": ["string", "boolean"]} for f in fields}}
        tool = _write_tool(
            name, desc,
            {"type": "object", "properties": props,
             "required": ["rule_id"] if op != "create" else []},
            _h_rule_change, prepare=_prepare_rule_change)
        # 注入内部参数模板（调用时由 orchestrator 补全）
        tool.internal = {"_resource": resource, "_op": op}
        return tool

    TOOLS.extend([
        rule_tool("create_nat_rule", "nat", "create",
                  "新建 NAT 策略（SNAT/DNAT）。data 需含 name 及对应字段：SNAT 填 translated_addr；DNAT 填 dst_addr(公网IP:端口) 与 translated_addr(内网IP:端口)",
                  NAT_FIELDS),
        rule_tool("update_nat_rule", "nat", "update", "修改 NAT 策略字段", NAT_FIELDS),
        rule_tool("delete_nat_rule", "nat", "delete", "删除 NAT 策略", NAT_FIELDS),
        rule_tool("create_acl_rule", "acl", "create",
                  "新建访问控制策略。data 需含 name、src/dst zone 与地址、service、action(allow/deny)",
                  ACL_FIELDS),
        rule_tool("update_acl_rule", "acl", "update", "修改访问控制策略字段（如停用 enabled=false、收紧匹配域）", ACL_FIELDS),
        rule_tool("delete_acl_rule", "acl", "delete", "删除访问控制策略", ACL_FIELDS),
        rule_tool("create_user_binding", "binding", "create", "新建 IP-MAC 用户绑定（user/ip/mac 必填）", BIND_FIELDS),
        rule_tool("update_user_binding", "binding", "update", "修改用户绑定", BIND_FIELDS),
        rule_tool("delete_user_binding", "binding", "delete", "删除用户绑定", BIND_FIELDS),
    ])


_register_write_tools()

TOOLS_BY_NAME = {t.name: t for t in TOOLS}
TOOL_SCHEMAS = [t.schema() for t in TOOLS]
