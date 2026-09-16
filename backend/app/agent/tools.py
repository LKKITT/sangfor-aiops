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
from app.services import personal_kb_service
from app.services import update_service, upgrade_advisor
from app.services import zhuge_kb_service
from app.services.analyzer import run_checks, check_rule_conflicts
from app.agent import guardrails

ToolHandler = Callable[[DeviceClient, dict, dict], Awaitable[Any]]


def _rule_brief(r: dict) -> str:
    return (f"{r.get('name', r.get('id'))} [{r.get('id')}] {r.get('src_zone', 'any')}:{r.get('src_addr', 'any')}"
            f" → {r.get('dst_zone', 'any')}:{r.get('dst_addr', 'any')} 服务={r.get('service', 'any')}"
            f" 动作={r.get('action', '')} 启用={r.get('enabled', True)}")


class Tool:
    def __init__(self, name: str, description: str, parameters: dict, handler: ToolHandler,
                 write: bool = False, prepare: ToolHandler | None = None,
                 device_type: str | None = None, needs_device: bool = True):
        """device_type: None=通用, 'af'=仅AF, 'ac'=仅AC；
        needs_device: False=执行/生成计划不需要连接设备（知识库沉淀、添加设备等），
        编排器将跳过设备登录——绑定设备不在线也不影响执行。"""
        self.name = name
        self.description = description
        self.parameters = parameters
        self.handler = handler
        self.write = write
        self.prepare = prepare
        self.device_type = device_type
        self.needs_device = needs_device
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
                    f"磁盘 {d['disk_usage']}%，会话 {d['session_count']}")
    return d


async def _h_interfaces(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return [i.to_dict() for i in await client.get_interfaces()]


async def _h_zones(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return await client.get_zones()


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
    kw = str(args.get("keyword", "")).strip()
    return [b.to_dict() for b in await client.get_user_bindings(keyword=kw)]


async def _h_ipmac_bindings(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    kw = str(args.get("keyword", "")).strip()
    return [b.to_dict() for b in await client.get_ipmac_bindings(keyword=kw)]


async def _h_objects(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    rows = [o.to_dict() for o in await client.get_network_objects()]
    kw = str(args.get("keyword", "")).strip().lower()
    if kw:
        rows = [r for r in rows if kw in json.dumps(r, ensure_ascii=False).lower()]
    return rows


async def _h_services(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    rows = [s.to_dict() for s in await client.get_services()]
    kw = str(args.get("keyword", "")).strip().lower()
    if kw:
        rows = [r for r in rows if kw in json.dumps(r, ensure_ascii=False).lower()]
    return rows


async def _h_whiteblacklist(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    """获取黑白名单列表。优先通过 whiteblacklist 端点，失败时降级返回空列表。"""
    try:
        data = await client._request("GET", f"/api/v1/namespaces/{client.namespace}/whiteblacklist",
                                     {"_start": 0, "_length": 200})
        rows = client._rows(data) if hasattr(client, '_rows') else (data or [])
        kw = str(args.get("keyword", "")).strip().lower()
        if kw:
            rows = [r for r in rows if kw in json.dumps(r, ensure_ascii=False).lower()]
        return rows
    except Exception:
        return [{"url": "（端点不可用/无权限）", "type": "", "enable": False, "description": ""}]


async def _h_routes(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return [r.to_dict() for r in await client.get_static_routes()]


async def _h_checkup(client: DeviceClient, args: dict, device: dict) -> dict:
    snapshot = await client.snapshot_config()
    status = (await client.get_status()).to_dict()
    report = run_checks(snapshot, status, device.get("type", ""))
    report["summary_text"] = (
        f"体检得分 {report['score']}/100（{report['grade']}）：高危 {report['counts']['high']} 项、"
        f"中危 {report['counts']['medium']} 项、低危 {report['counts']['low']} 项")
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
BIND_FIELDS = ("user", "ip", "mac", "binding_type", "comment", "enabled", "noauth", "limitlogon")
OBJECT_FIELDS = ("name", "type", "members", "comment")
SERVICE_FIELDS = ("name", "protocol", "ports", "comment")
WHITEBLACKLIST_FIELDS = ("url", "type", "enable", "description")

# 变更计划展示所需的资源映射
_RESOURCE_SECTION = {"nat": ("nat_rules", "NAT 策略"), "acl": ("acl_rules", "访问控制策略"),
                     "binding": ("user_bindings", "用户绑定"),
                     "object": ("objects", "网络对象"), "service": ("services", "自定义服务"),
                     "whiteblacklist": ("whiteblacklist", "黑白名单")}
_RESOURCE_FIELDS = {"nat": NAT_FIELDS, "acl": ACL_FIELDS, "binding": BIND_FIELDS,
                    "object": OBJECT_FIELDS, "service": SERVICE_FIELDS,
                    "whiteblacklist": WHITEBLACKLIST_FIELDS}


async def _acl_missing_refs_note(client, data: dict) -> str:
    """预检 ACL 引用的地址组/服务：缺失项提示确认后自动创建（或需人工调整的项）。

    地址：裸 IP/网段不存在 → 将自动创建同名网络对象；服务：按 自定义 → 预定义
    顺序检查，端口形态（如 TCP5211）不存在 → 将自动创建自定义服务，纯名称均
    不存在 → 提示改用已有服务名或指定端口。仅 AF 设备调用；查询失败不阻断计划。
    """
    from app.adapters.af_rest import AfRestClient
    notes = []
    if str(data.get("src_addr") or "").strip() or str(data.get("dst_addr") or "").strip():
        try:
            objects = {o.name for o in await client.get_network_objects()}
        except Exception:   # noqa: BLE001 —— 查询失败不影响计划生成
            objects = None
        if objects is not None:
            missing = []
            for key in ("src_addr", "dst_addr"):
                for part in (p.strip() for p in str(data.get(key) or "").replace("，", ",").split(",")):
                    if (part and part not in objects and part not in missing
                            and AfRestClient._ip_like(part)):
                        missing.append(part)
            if missing:
                notes.append("引用的 IP组 " + "、".join(missing)
                             + " 在设备上不存在，确认执行时将自动创建同名网络对象（成员为对应 IP/网段）")
    if str(data.get("service") or "").strip():
        try:
            custom = {s.name for s in await client.get_services()}
        except Exception:   # noqa: BLE001
            custom = None
        try:
            predefined = {s.name for s in await client.get_predefined_services()}
        except Exception:   # noqa: BLE001
            predefined = None
        if custom is not None:
            to_create, not_found = [], []
            for part in (p.strip() for p in str(data.get("service") or "").replace("，", ",").split(",")):
                if not part or part in custom or part in predefined or part in to_create:
                    continue
                if AfRestClient._parse_port_spec(part):
                    to_create.append(part)
                else:
                    not_found.append(part)
            if to_create:
                notes.append("引用的服务 " + "、".join(to_create)
                             + " 不存在，确认执行时将按端口自动创建自定义服务后再下发")
            if not_found:
                notes.append("引用的服务 " + "、".join(not_found)
                             + " 在自定义/预定义服务中均不存在，请改用已有服务名或指定端口（如 TCP5211）自动创建")
    return "\n".join(notes)


async def _prepare_rule_change(client: DeviceClient, args: dict, device: dict) -> dict:
    """通用变更计划生成：返回含变更前后对照的确认卡片数据。"""
    resource = args["_resource"]
    op = args["_op"]
    section, resource_cn = _RESOURCE_SECTION[resource]
    snapshot = await client.snapshot_config()
    before = None
    target_id = str(args.get("rule_id", ""))
    for r in snapshot.get(section) or []:
        if str(r.get("id")) == target_id or str(r.get("url")) == target_id:
            before = r
            break
    data = {k: v for k, v in (args.get("data") or {}).items() if k in _RESOURCE_FIELDS[resource]}
    if resource == "acl" and device.get("type", "") == "af":
        # 地址字段约定：任意地址 = 内置网络对象「全部」，禁止字面量 any（设备会报对象不存在）；
        # 在计划展示与下发数据（含待确认动作落库的 args）两侧同时规范化
        for key in ("src_addr", "dst_addr"):
            if str(data.get(key, "")).strip().lower() in ("any", "any4", "任意", "所有"):
                data[key] = "全部"
                if isinstance(args.get("data"), dict) and key in args["data"]:
                    args["data"][key] = "全部"
    after = dict(before or {})
    after.update(data)
    title = {"create": f"新建{resource_cn}", "update": f"修改{resource_cn}「{before.get('name', target_id) if before else target_id}」",
             "delete": f"删除{resource_cn}「{before.get('name', target_id) if before else target_id}」"}[op]
    # 定向核实：只检查这一条配置与现有配置的冲突/重叠（不做全量体检）
    conflicts = []
    if op in ("create", "update") and resource in ("nat", "acl") and after:
        section = _RESOURCE_SECTION[resource][0]
        conflicts = check_rule_conflicts(resource, after, snapshot.get(section) or [])
    warning = _change_warning(resource, op, before, after)
    if (resource == "acl" and op in ("create", "update")
            and device.get("type", "") == "af"):
        note = await _acl_missing_refs_note(client, after)
        if note:
            warning = f"{warning}\n\n{note}" if warning else note
    return {
        "title": title,
        "resource": resource, "resource_cn": resource_cn, "op": op, "target_id": target_id,
        "before": before, "after": after if op != "delete" else None,
        "fields": sorted(data.keys()),
        "conflicts": conflicts,
        "warning": warning,
    }


def _change_warning(resource: str, op: str, before: dict | None, after: dict | None) -> str:
    if op == "delete":
        return "删除操作即时生效且影响该策略覆盖的全部流量，请确认业务已迁移"
    if resource == "acl" and after and after.get("action") == "allow" and \
            str(after.get("service", "")).lower() in ("any", ""):
        return "注意：该策略放行全部服务，请确认符合最小权限原则"
    if resource == "nat" and op == "update" and before and before.get("type") == "DNAT":
        return "注意：修改 DNAT 映射会影响对外发布的业务可达性"
    if resource == "object" and after and str(after.get("members", "")).find("0.0.0.0") >= 0:
        return "注意：对象包含 0.0.0.0/0 等价于任意地址，引用它的策略将被放大权限"
    if resource == "service" and after and after.get("ports"):
        return "注意：修改服务端口会影响所有引用该服务的策略"
    if resource == "binding" and after:
        return ("注意：免认证开启后该用户来源将不经认证直接放行，请确认场景；"
                "绑定默认永久有效" if (after.get("noauth") or after.get("limitlogon")) else
                "绑定默认永久有效；如需免认证/限制登录请在卡片中调整")
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


async def _h_add_device(client: DeviceClient, args: dict, device: dict) -> dict:
    """在执行阶段真正添加设备到数据库。"""
    name = str(args.get("name", "")).strip()
    if not name:
        return {"error": "设备名称不能为空"}
    # 检查是否已存在同名设备
    existing = db.list_devices()
    if any(d["name"] == name for d in existing):
        return {"error": f"已存在同名设备「{name}」"}
    dtype = str(args.get("type", "af"))
    dmode = str(args.get("mode", "real"))
    base_url = str(args.get("base_url", "")).strip()
    username = str(args.get("username", "")).strip()
    password = str(args.get("password", "")).strip()
    readonly = bool(args.get("readonly", False))
    device = db.upsert_device({
        "id": db.new_id("dev_"), "name": name, "type": dtype,
        "mode": dmode, "base_url": base_url,
        "username": username, "password": password,
        "readonly": int(readonly), "settings_json": "{}", "created_at": db.now(),
    })
    db.audit("device.create", {"name": name, "mode": dmode, "source": "agent_dialog"})
    return {"ok": True, "device_id": device["id"], "name": name,
            "message": f"设备「{name}」已添加成功"}


async def _p_add_device(client: DeviceClient, args: dict, device: dict) -> dict:
    """添加设备确认计划：测试连接并生成确认卡片。"""
    name = str(args.get("name", "")).strip()
    if not name:
        return {"error": "设备名称不能为空"}
    existing = db.list_devices()
    if any(d["name"] == name for d in existing):
        return {"error": f"已存在同名设备「{name}」"}
    dtype = str(args.get("type", "af"))
    dmode = str(args.get("mode", "real"))
    base_url = str(args.get("base_url", "")).strip()
    username = str(args.get("username", "")).strip()
    password = str(args.get("password", "")).strip()
    readonly = bool(args.get("readonly", False))

    # 真实设备时尝试测试连接
    test_result = ""
    if dmode == "real" and base_url:
        try:
            from app.adapters.factory import create_client
            tmp_device = {
                "id": "_test_", "name": name, "type": dtype, "mode": dmode,
                "base_url": base_url, "username": username, "password": password,
                "readonly": 0, "settings_json": "{}", "created_at": db.now(),
            }
            tmp_client = create_client(tmp_device)
            await tmp_client.login()
            st = await tmp_client.get_status()
            test_result = f"连接成功：{st.model}，版本 {st.sw_version}"
            await tmp_client.aclose()
        except Exception as e:
            test_result = f"连接测试：{e}"
    else:
        test_result = "模拟器设备，跳过连接测试"

    type_label = {"af": "下一代防火墙 AF", "ac": "上网行为管理 AC",
                  "scp": "云计算平台 SCP"}.get(dtype, dtype)
    mode_label = "真实设备" if dmode == "real" else "内置模拟器"
    info_lines = [
        f"- **名称**：{name}",
        f"- **类型**：{type_label}",
        f"- **接入方式**：{mode_label}",
    ]
    if dmode == "real":
        info_lines.append(f"- **设备地址**：{base_url}")
        if dtype == "af":
            info_lines.append(f"- **API 账号**：{username}")
            info_lines.append(f"- **API 密码**：{'*' * len(password) if password else '（空）'}")

    return {
        "title": f"添加设备「{name}」",
        "resource": "device", "resource_cn": "设备", "op": "create",
        "target_id": "",
        "before": None,
        "after": {"name": name, "type": dtype, "mode": dmode, "base_url": base_url,
                  "username": username, "readonly": readonly},
        "fields": ["name", "type", "mode", "base_url", "username", "readonly"],
        "conflicts": [],
        "warning": "请确认设备信息正确，添加后即可通过对话管理该设备。",
        "detail": "\n".join(info_lines) + f"\n\n**连接测试**：{test_result}",
    }


async def _h_search_personal_kb(client: DeviceClient, args: dict, device: dict) -> dict:
    """检索本地个人知识库：分词加权评分（中文 2-gram），不同措辞也能命中，毫秒级。"""
    from app import db
    keyword = str(args.get("keyword") or "").strip()
    rows = db.search_kb_entries(keyword, limit=5)
    if not rows:
        return {"matches": 0, "entries": [],
                "_llm_summary": ("本地个人知识库未命中相关词条。可换更短的核心词再试一次"
                                 "（如只传 'HA 主备' / '内存 虚高'）；仍未命中可调用 "
                                 "search_official_knowledge 检索官方知识库作答。")}
    entries = [{"topic": e.get("topic"), "category": e.get("category"),
                "summary": e.get("summary"),
                "key_points": (e.get("key_points") or [])[:5],
                "content": (e.get("content_md") or "")[:600],
                "tags": (e.get("tags") or [])[:5],
                "aliases": (e.get("aliases") or [])[:5],
                "relevance": e.get("score"),
                "matched_keywords": e.get("matched")} for e in rows]
    topics = "；".join(e["topic"] for e in entries)
    return {"matches": len(entries), "entries": entries,
            "_llm_summary": (f"本地个人知识库命中 {len(entries)} 条（按相关度排序：{topics}）。"
                             f"请优先基于高相关度词条作答，并向用户注明出自个人知识库；"
                             f"如本地信息不足以完整回答，再调用 search_official_knowledge 查询官方知识库补充。")}


async def _h_search_kb(client: DeviceClient, args: dict, device: dict) -> dict:
    """查询深信服官方知识库（诸葛小T），按当前设备产品线定向检索。"""
    question = str(args.get("question", "")).strip()
    product = zhuge_kb_service.product_from_device_type(device.get("type", ""))
    # 附上设备当前版本：官方知识库常按版本区间区分答案，可减少其反问澄清
    version = ""
    try:
        version = (await client.get_status()).sw_version
    except Exception:   # noqa: BLE001 —— 拿不到版本不阻塞检索
        pass
    full_question = f"{question}（设备当前版本：{product} {version}）" if version else question
    result = await zhuge_kb_service.ask_official_kb(full_question, product=product)
    refs_text = "\n".join(
        f"- [{r['title']}]({r['url']})" if r.get("url") else f"- {r['title']}"
        for r in result["references"]) or "-（无引用条目）"
    if result.get("clarification"):
        result["_llm_summary"] = (
            f"官方知识库没有直接作答，而是提出了澄清问题：「{result['answer']}」。"
            f"请结合当前设备的真实信息（版本号等）直接回答用户，"
            f"或把版本号等条件补进问题后再次调用本工具检索。")
    elif result["status"] == "ok":
        result["_llm_summary"] = (
            f"官方知识库（诸葛小T · {result['product']}）已返回答案，引用 {len(result['references'])} 条。"
            f"请归纳后作答，并在末尾附「官方参考」；带链接的引用以 [标题](url) 形式原样保留：\n{refs_text}")
    else:
        result["_llm_summary"] = (f"官方知识库查询未成功（{result.get('reason', '未知原因')}）。"
                                  f"请如实告知用户，不要编造知识库内容，可结合设备数据与自身知识回答。")
    return result


async def _h_record_kb(client: DeviceClient, args: dict, device: dict) -> dict:
    """把当前对话标记为待沉淀（编排器在执行后触发后台提炼）。"""
    note = str(args.get("note") or "").strip()
    result = {"ok": True, "message": "已登记：对话结束后将自动提炼为个人知识库词条"}
    if note:
        result["note"] = note[:200]
    result["_llm_summary"] = ("已登记把本次对话沉淀到个人知识库，对话结束后会自动提炼知识词条。"
                              "请告知用户可在『个人知识库』页面查看结果。")
    return result


async def _p_record_kb(client: DeviceClient, args: dict, device: dict) -> dict:
    note = str(args.get("note") or "").strip()
    return {
        "title": "记录当前对话到个人知识库",
        "resource": "kb_record", "resource_cn": "知识库沉淀", "op": "create", "target_id": "",
        "before": None,
        "after": {"note": note[:200]} if note else {},
        "fields": ["note"] if note else [],
        "conflicts": [],
        "warning": "",
        "detail": ("确认后，本次对话内容将由 LLM 提炼为个人知识库词条"
                   + (f"（重点：{note[:100]}）" if note else "") + "，可在『个人知识库』页面查看。"),
    }


async def _h_ingest_url(client: DeviceClient, args: dict, device: dict) -> dict:
    """抓取用户指定链接，提炼内容沉淀到个人知识库。"""
    url = str(args.get("url", "")).strip()
    note = str(args.get("note") or "").strip()
    result = await personal_kb_service.ingest_url(url, note)
    if result.get("status") == "ok":
        if not result.get("saved") and result.get("updated"):
            # 全部被合并进已有词条：明确告知去向，避免"提示完成但界面没有新词条"的困惑
            topics = "、".join(f"《{t}》" for t in result.get("updated_topics", [])[:5])
            result["_llm_summary"] = (
                f"链接内容提炼完成，但与知识库已有词条主题相同，未新建词条，"
                f"已合并更新 {result['updated']} 条：{topics}。"
                f"请明确告知用户：本次没有新增词条，更新内容已并入上述词条"
                f"（来源链接附在词条引用中），可在『个人知识库』搜索这些主题查看，"
                f"或按「最近更新」排序查看。")
        else:
            result["_llm_summary"] = (
                f"已从链接「{result['title']}」提炼知识：新增 {result['saved']} 条词条"
                f"（更新 {result['updated']} 条），来源链接已附在词条引用中。"
                f"请告知用户可在『个人知识库』页面查看。")
    elif result.get("status") == "skipped":
        result["_llm_summary"] = f"链接沉淀未完成：{result.get('reason')}。请如实告知用户。"
    else:
        result["_llm_summary"] = f"链接沉淀未成功（{result.get('reason', '未知原因')}）。请如实告知用户，可建议换用内容更具体的详情页链接。"
    return result


async def _p_ingest_url(client: DeviceClient, args: dict, device: dict) -> dict:
    url = str(args.get("url", "")).strip()
    note = str(args.get("note") or "").strip()
    return {
        "title": "沉淀网页链接内容到个人知识库",
        "resource": "kb_ingest", "resource_cn": "知识库沉淀", "op": "create", "target_id": "",
        "before": None,
        "after": {"url": url, **({"note": note[:200]} if note else {})},
        "fields": ["url"] + (["note"] if note else []),
        "conflicts": [],
        "warning": "",
        "detail": (f"确认后将抓取该页面内容（{url}），经 LLM 提炼为知识词条沉淀到个人知识库，"
                   "词条引用会附带来源链接。同主题已有词条会被更新为最新内容。"),
    }


# ============================ SCP 云计算平台（只读） ============================

async def _h_scp_clusters(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    clusters = await client.get_scp_clusters()
    return clusters


async def _h_scp_hosts(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return await client.get_scp_hosts(cluster_id=str(args.get("cluster_id") or ""),
                                      keyword=str(args.get("keyword") or ""))


async def _h_scp_host_ifs(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return await client.get_scp_host_interfaces(str(args["host_id"]))


async def _h_scp_vms(client: DeviceClient, args: dict, device: dict) -> dict:
    """虚拟机查询：支持使用率过滤，返回瘦身列表 + 统计摘要（避免全量 JSON 溢出）。"""
    min_cpu = float(args.get("min_cpu_usage") or 0)
    min_mem = float(args.get("min_memory_usage") or 0)
    vms = await client.get_scp_vms(host_id=str(args.get("host_id") or ""),
                                   status=str(args.get("status") or ""),
                                   keyword=str(args.get("keyword") or ""),
                                   limit=int(args.get("limit") or 200))
    total = len(vms)
    if min_cpu > 0:
        vms = [v for v in vms if (v.get("res") or {}).get("cpu", {}).get("ratio", 0) >= min_cpu]
    if min_mem > 0:
        vms = [v for v in vms if (v.get("res") or {}).get("memory", {}).get("ratio", 0) >= min_mem]
    # 瘦身：只保留回答所需的字段（全量 JSON 会截断导致数据不完整）
    slim = [{"name": v.get("name"), "status": v.get("status"),
             "ip": ", ".join(v.get("ips") or []) or next(
                 (n.get("ip_address") for n in v.get("networks") or [] if n.get("ip_address")), ""),
             "host": v.get("host_name"), "os": v.get("os_display") or v.get("os_name"),
             "cpu_ratio": (v.get("res") or {}).get("cpu", {}).get("ratio"),
             "mem_ratio": (v.get("res") or {}).get("memory", {}).get("ratio"),
             "spec": f"{v.get('cores')}核/{round((v.get('memory_mb') or 0) / 1024, 1)}G",
             "id": v.get("id")} for v in vms]
    top_mem = sorted(slim, key=lambda x: -(x.get("mem_ratio") or 0))[:5]
    result = {"total_in_platform": total, "matched": len(slim),
              "filter": {"min_cpu_usage": min_cpu or None, "min_memory_usage": min_mem or None},
              "vms": slim}
    mem_desc = f"，其中内存使用率≥{min_mem}% 的 {len(slim)} 台" if min_mem else ""
    cpu_desc = f"CPU≥{min_cpu}% 的 {len(slim)} 台" if min_cpu else ""
    top_desc = "；内存使用率 Top5：" + "、".join(
        f"{t['name']}({t['mem_ratio']}%)" for t in top_mem) if top_mem else ""
    result["_llm_summary"] = (
        f"平台共 {total} 台虚拟机{mem_desc}{cpu_desc}。以上为匹配列表（已含名称/状态/IP/宿主/系统/使用率）。"
        f"请基于该列表作答，不要再次调用本工具或查询物理机。{top_desc}")
    return result


async def _h_scp_vm_detail(client: DeviceClient, args: dict, device: dict) -> dict:
    detail = await client.get_scp_vm_detail(str(args["server_id"]))
    nets = detail.get("networks") or []
    detail["_llm_summary"] = (
        f"云主机「{detail.get('name')}」：{detail.get('status')}，"
        f"{detail.get('cores')}核/{detail.get('memory_mb')}MB，"
        f"IP：{', '.join(n.get('ip_address') or '' for n in nets if n.get('ip_address')) or '无'}，"
        f"所在物理机：{detail.get('host_name') or '未知'}")
    return detail


async def _h_scp_storages(client: DeviceClient, args: dict, device: dict) -> list[dict]:
    return await client.get_scp_storages()


async def _h_switch_device(client: DeviceClient, args: dict, device: dict) -> dict:
    """列出所有可用设备，供用户选择切换。"""
    devices = db.list_devices()
    return {
        "available_devices": [
            {"id": d["id"], "name": d["name"], "type": d["type"], "mode": d["mode"]}
            for d in devices
        ],
        "current_device": device.get("name", ""),
        "_llm_summary": f"当前设备：{device.get('name')}。可用设备：{', '.join(d['name'] for d in devices)}。"
                        f"请告知用户可在界面上方设备选择器中切换目标设备。"
                        f"如需操作不存在的设备，请引导用户先在「设备管理」页面添加。",
    }


# ---- 工具注册 ----

def _write_tool(name: str, desc: str, params: dict, handler: ToolHandler,
                prepare: ToolHandler | None = None,
                device_type: str | None = None, needs_device: bool = True) -> Tool:
    return Tool(name, desc, params, handler, write=True, prepare=prepare,
                device_type=device_type, needs_device=needs_device)


def rule_tool(name: str, desc: str, params: dict, handler: ToolHandler,
              prepare: ToolHandler | None = None) -> Tool:
    return Tool(name, desc, params, handler, write=True, prepare=prepare)


# 按设备类型过滤的工具列表
def get_tools(device_type: str = "") -> list[Tool]:
    """返回指定设备类型的工具列表。device_type='' 或 'af' 返回 AF 工具，'ac' 返回 AC 工具。"""
    return [t for t in TOOLS if t.device_type is None or t.device_type == device_type]


def get_tool_schemas(device_type: str = "") -> list[dict]:
    """返回指定设备类型的工具 schema。"""
    return [t.schema() for t in get_tools(device_type)]


def get_tools_by_name(device_type: str = "") -> dict[str, Tool]:
    return {t.name: t for t in get_tools(device_type)}


TOOLS: list[Tool] = [
    Tool("get_device_status", "获取设备运行状态：版本、型号、CPU/内存/磁盘使用率、会话数、运行时间", {"type": "object", "properties": {}}, _h_status),
    Tool("get_interfaces", "获取网络接口列表：名称、区域、IP、状态、速率、收发流量", {"type": "object", "properties": {}}, _h_interfaces),
    Tool("get_zones", "获取安全区域（Zone）定义列表：区域名称、转发类型、绑定接口。支持AF防火墙区域信息查询。", {
        "type": "object",
        "properties": {},
    }, _h_zones, device_type='af'),
    Tool("get_nat_rules", "获取 NAT 策略列表（SNAT/DNAT），可按关键词过滤", {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "策略名称/源地址/目的地址过滤"}},
    }, _h_nat, device_type='af'),
    Tool("get_acl_rules", "获取访问控制（应用控制）策略列表，可按关键词过滤", {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "过滤关键词"}},
    }, _h_acl),
    Tool("get_user_bindings", "获取用户绑定信息（AC，来自user-bindinfo接口），包含用户名、IP、MAC、绑定类型、备注。可按IP/MAC/用户名关键词过滤。", {
    "type": "object",
    "properties": {"keyword": {"type": "string", "description": "搜索关键词（IP/MAC/用户名），为空则返回全部"}},
}, _h_bindings),
    Tool("get_ipmac_bindings", "获取纯IP/MAC绑定信息（AC，来自ipmac-bindinfo接口），包含IP、MAC地址、描述。可按IP/MAC关键词过滤。需与get_user_bindings配合使用，分别展示用户绑定和IP/MAC绑定结果。", {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "搜索关键词（IP/MAC），为空则返回全部"}},
    }, _h_ipmac_bindings, device_type='ac'),
    Tool("get_network_objects", "获取网络对象（IP 地址组/范围组）列表，可按关键词过滤", {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "过滤关键词"}},
    }, _h_objects, device_type='af'),
    Tool("get_services", "获取自定义服务（协议+端口）列表", {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "过滤关键词"}},
    }, _h_services, device_type='af'),
    Tool("get_whiteblacklist", "获取黑白名单列表（AF 防火墙），可按关键词过滤。返回字段：url=IP/域名/URL, type=BLACK(黑名单)/WHITE(白名单), enable=true/false, description=备注。API仅支持永久封锁，无临时封锁类型。", {
        "type": "object",
        "properties": {"keyword": {"type": "string", "description": "过滤关键词，如 IP/地址/备注"}},
    }, _h_whiteblacklist, device_type='af'),
    Tool("get_static_routes", "获取静态路由列表", {"type": "object", "properties": {}}, _h_routes, device_type='af'),
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
    Tool("list_available_devices", "列出所有可用的设备列表，供用户选择切换目标设备。当用户提到其他设备名或想操作另一个设备时使用。",
         {"type": "object", "properties": {}}, _h_switch_device),
    _write_tool("record_to_kb",
                "把当前对话记录沉淀到个人知识库（用户明确要求『记录/保存/沉淀到知识库』时使用）。可选 note 说明希望重点记录的内容。生成确认卡片供用户确认后登记。",
                {"type": "object",
                 "properties": {"note": {"type": "string",
                                         "description": "可选：用户希望重点记录的内容或备注"}},
                 }, _h_record_kb, prepare=_p_record_kb, needs_device=False),
    _write_tool("ingest_url_to_kb",
                "抓取用户提供的网页链接，把页面内容提炼沉淀到个人知识库（用户给出 URL 并要求『把这个链接录入/沉淀到知识库』时使用）。深信服官方案例库链接（support.sangfor.com.cn/cases/...）走社区爬虫认证读取，效果最佳。生成确认卡片供用户确认后执行。",
                {"type": "object",
                 "properties": {"url": {"type": "string", "description": "要沉淀的网页链接（http/https）"},
                                "note": {"type": "string", "description": "可选：用户希望重点关注的方向"}},
                 "required": ["url"],
                 }, _h_ingest_url, prepare=_p_ingest_url, needs_device=False),
    Tool("search_personal_kb",
         "检索本地个人知识库（此前沉淀的词条与官方案例知识）。设备功能作用、故障排查类问题优先检索本地；keyword 传 2~3 个空格分隔的核心词（如：HA 主备 / 内存 虚高 / 445 端口），不要传整句",
         {"type": "object",
          "properties": {"keyword": {"type": "string",
                                     "description": "核心技术关键词，如：HA 主备、内存虚高、445 端口、ARP 冲突"}},
          "required": ["keyword"]},
         _h_search_personal_kb),
    Tool("search_official_knowledge",
         "检索深信服官方知识库（诸葛小T）：适用于产品配置方法、故障排查思路、版本兼容性、官方最佳实践等通用技术问题，回答附带官方引用来源。设备实时数据（状态/策略/资源）请使用设备查询工具，不要用本工具。",
         {"type": "object",
          "properties": {"question": {"type": "string",
                                      "description": "要检索的问题，用一句完整的中文技术问题描述"}},
          "required": ["question"]},
         _h_search_kb),
    # ---- SCP 云计算平台（只读查询，device_type='scp'） ----
    Tool("get_scp_clusters", "获取 SCP 云计算平台的集群列表：名称/状态/版本/类型，及 CPU、内存、存储资源的总量与使用率", {"type": "object", "properties": {}}, _h_scp_clusters, device_type='scp'),
    Tool("get_scp_hosts", "获取 SCP 平台的物理机（HCI 节点）列表：IP/状态/所属集群/CPU 内存存储使用率/GPU/告警数。仅在用户明确询问物理机/宿主机/节点时使用；查询虚拟机资源使用情况请用 get_scp_vms 的过滤参数，不要先查物理机",
         {"type": "object",
          "properties": {"cluster_id": {"type": "string", "description": "可选：按集群 ID 过滤"},
                         "keyword": {"type": "string", "description": "可选：按名称或 IP 过滤"}}},
         _h_scp_hosts, device_type='scp'),
    Tool("get_scp_host_interfaces", "获取 SCP 平台指定物理机的网口列表：网口名/功能（管理/业务/存储/数据通信口）/功能 IP/网关/MAC/VLAN/速率",
         {"type": "object",
          "properties": {"host_id": {"type": "string", "description": "物理机 ID（从 get_scp_hosts 获取）"}},
          "required": ["host_id"]},
         _h_scp_host_ifs, device_type='scp'),
    Tool("get_scp_vms", "获取 SCP 平台的虚拟机（云主机）列表与 CPU/内存使用率。支持按物理机/状态/名称过滤，以及按使用率阈值过滤（如『内存使用率超过80%的虚拟机』直接传 min_memory_usage=80，一次调用即可得到精准结果）。返回已含名称/状态/IP/宿主/系统/使用率，无需重复调用或另查物理机",
         {"type": "object",
          "properties": {"host_id": {"type": "string", "description": "可选：按物理机 ID 过滤"},
                         "status": {"type": "string", "description": "可选：按状态过滤（如 running）"},
                         "keyword": {"type": "string", "description": "可选：按名称或 IP 过滤"},
                         "min_cpu_usage": {"type": "number", "description": "可选：CPU 使用率下限（%），如 80"},
                         "min_memory_usage": {"type": "number", "description": "可选：内存使用率下限（%），如 80"},
                         "limit": {"type": "integer", "description": "可选：返回上限，默认 200"}}},
         _h_scp_vms, device_type='scp'),
    Tool("get_scp_vm_detail", "获取 SCP 平台指定虚拟机的配置详情：CPU/内存/磁盘/网卡（MAC、IP、VPC、子网、端口组）/所属物理机/高级参数",
         {"type": "object",
          "properties": {"server_id": {"type": "string", "description": "虚拟机 ID（从 get_scp_vms 获取）"}},
          "required": ["server_id"]},
         _h_scp_vm_detail, device_type='scp'),
    Tool("get_scp_storages", "获取 SCP 平台的存储列表：名称/类型/状态/总量与使用率/关联主机", {"type": "object", "properties": {}}, _h_scp_storages, device_type='scp'),
    _write_tool("add_device", "添加新设备到系统。用户提供设备名称、类型(AF/AC)、接入方式(模拟器/真实设备)、地址、账号密码等信息。生成确认卡片供用户确认后执行添加。",
                {"type": "object",
                 "properties": {
                     "name": {"type": "string", "description": "设备名称，如：总部-AF-01"},
                     "type": {"type": "string", "description": "设备类型：af（防火墙）/ ac（上网行为管理）/ scp（云计算平台，AccessKey+SecretKey 只读接入），默认 af"},
                     "mode": {"type": "string", "description": "接入方式：real（真实设备）/ simulator（内置模拟器），默认 real"},
                     "base_url": {"type": "string", "description": "设备地址，真实设备必填，如 https://192.168.1.1"},
                     "username": {"type": "string", "description": "API 账号（AF 真实设备需要）"},
                     "password": {"type": "string", "description": "API 密码（AF 真实设备）或共享密钥（AC 设备）"},
                     "readonly": {"type": "boolean", "description": "是否只读模式，默认 false"},
                 },
                 "required": ["name"],
                 }, _h_add_device, prepare=_p_add_device, needs_device=False),
]


def _register_write_tools() -> None:
    common_id = {"rule_id": {"type": "string", "description": "策略/记录 ID"}}

    def rule_tool(name: str, resource: str, op: str, desc: str, fields: tuple,
                  device_type: str | None = None) -> Tool:
        props: dict[str, Any] = dict(common_id)
        props["data"] = {"type": "object",
                         "properties": {f: {"type": ["string", "boolean"]} for f in fields}}
        tool = _write_tool(
            name, desc,
            {"type": "object", "properties": props,
             "required": ["rule_id"] if op != "create" else []},
            _h_rule_change, prepare=_prepare_rule_change,
            device_type=device_type)
        # 注入内部参数模板（调用时由 orchestrator 补全）
        tool.internal = {"_resource": resource, "_op": op}
        return tool

    TOOLS.extend([
        # NAT 策略：仅 AF 防火墙支持
        rule_tool("create_nat_rule", "nat", "create",
                  "新建 NAT 策略（SNAT/DNAT）。data 需含 name 及对应字段：SNAT 填 translated_addr；DNAT 填 dst_addr(公网IP:端口) 与 translated_addr(内网IP:端口)",
                  NAT_FIELDS, device_type='af'),
        rule_tool("update_nat_rule", "nat", "update", "修改 NAT 策略字段", NAT_FIELDS, device_type='af'),
        rule_tool("delete_nat_rule", "nat", "delete", "删除 NAT 策略", NAT_FIELDS, device_type='af'),
        # 访问控制策略：AF 和 AC 均支持
        rule_tool("create_acl_rule", "acl", "create",
                  "新建访问控制策略。data 需含 name、src/dst zone 与地址、service、action(allow/deny)",
                  ACL_FIELDS),
        rule_tool("update_acl_rule", "acl", "update", "修改访问控制策略字段（如停用 enabled=false、收紧匹配域）", ACL_FIELDS),
        rule_tool("delete_acl_rule", "acl", "delete", "删除访问控制策略", ACL_FIELDS),
        # 用户绑定：AF 和 AC 均支持
        rule_tool("create_user_binding", "binding", "create", "新建 IP-MAC 绑定（user/ip/mac 必填；AC 设备可带 noauth=免认证、limitlogon=限制登录，布尔值，默认均关闭即永久有效仅绑定）", BIND_FIELDS),
        rule_tool("update_user_binding", "binding", "update", "修改用户绑定", BIND_FIELDS),
        rule_tool("delete_user_binding", "binding", "delete", "删除用户绑定", BIND_FIELDS),
        # 网络对象：仅 AF 防火墙支持
        rule_tool("create_network_object", "object", "create",
                  "新建网络对象（IP组）。data：name 必填，members 为网段/IP 逗号分隔，comment 建议填写",
                  OBJECT_FIELDS, device_type='af'),
        rule_tool("update_network_object", "object", "update", "修改网络对象成员或备注", OBJECT_FIELDS, device_type='af'),
        rule_tool("delete_network_object", "object", "delete", "删除网络对象（需确认无策略引用）", OBJECT_FIELDS, device_type='af'),
        # 自定义服务：仅 AF 防火墙支持
        rule_tool("create_service", "service", "create",
                  "新建自定义服务。data：name 必填，protocol(TCP/UDP)，ports 如 80,443 或 9090-9092",
                  SERVICE_FIELDS, device_type='af'),
        rule_tool("update_service", "service", "update", "修改自定义服务端口或协议", SERVICE_FIELDS, device_type='af'),
        rule_tool("delete_service", "service", "delete", "删除自定义服务（需确认无策略引用）", SERVICE_FIELDS, device_type='af'),
        # 黑白名单：仅 AF 防火墙支持
        rule_tool("create_whiteblacklist", "whiteblacklist", "create",
                  "添加黑白名单条目。data：url=IP地址/域名，type=BLACK(黑名单)或WHITE(白名单)，"
                  "enable=true/false，description=备注。"
                  "注意：API仅支持永久封锁，不支持临时封锁。",
                  WHITEBLACKLIST_FIELDS, device_type='af'),
        rule_tool("update_whiteblacklist", "whiteblacklist", "update", "修改黑白名单条目", WHITEBLACKLIST_FIELDS, device_type='af'),
        rule_tool("delete_whiteblacklist", "whiteblacklist", "delete", "删除黑白名单条目", WHITEBLACKLIST_FIELDS, device_type='af'),
    ])


_register_write_tools()

TOOLS_BY_NAME = {t.name: t for t in TOOLS}
TOOL_SCHEMAS = [t.schema() for t in TOOLS]
