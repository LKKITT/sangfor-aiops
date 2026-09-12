"""升级建议引擎（确定性核心 + LLM 解说）。

结合设备当前版本、内置升级路线图、PSIRT 公告命中、EOL 状态、已知问题匹配，
给出是否升级 / 升级路径 / 升级时机 / 行动清单的结构化建议。
"""
from app.services import update_service
from app.services.knowledge import versions as kb

# 已知问题 → 触发条件（当前版本 + 症状关键词命中监控指标）
KNOWN_ISSUE_TRIGGERS = [
    {"product": "af", "version": "8.0.85", "metric": "mbuf_usage", "threshold": 60,
     "title": "mbuf 缓冲池占用偏高", "detail": "AF 8.0.85 存在邮件安全功能占满 mbuf 的已知问题，8.0.107 已彻底修复"},
    {"product": "af", "version": "8.0.95", "metric": "session_count", "threshold": -1,
     "title": "SNAT 端口池延迟回收", "detail": "SNAT 大网段场景端口池延迟 120 秒回收，可能引发端口耗尽"},
]


async def build_upgrade_advice(sw_version: str, status: dict | None = None,
                               device_name: str = "") -> dict:
    status = status or {}
    overview = await update_service.get_update_overview(sw_version)
    product, current = overview["product"], overview["current_version"]
    # 升级路径 = 内置知识库固化的官方技术升级链（完整可执行路径，不跳过中间版本）；
    # 升级价值（关键变更点）= 官方功能页版本条目优先（见 get_update_overview）
    path = kb.upgrade_path(product, current, overview["latest_version"])

    # ---- 是否升级的决策理由 ----
    reasons: list[dict] = []
    risk = "low"
    if overview["advisories_hit"]:
        risk = "high"
        for a in overview["advisories_hit"]:
            reasons.append({"level": "high", "type": "安全漏洞",
                            "text": f"当前版本命中安全公告 {a['id']}：{a['title']}（CVSS {a.get('cvss', '-')}），修复方案：{a.get('fixed_in', '升级版本')}"})
    if overview["eol"]["hit"]:
        risk = max_risk(risk, "high")
        reasons.append({"level": "high", "type": "生命周期",
                        "text": f"当前版本已停止维护（EOL）：{overview['eol']['detail']}"})
    for t in KNOWN_ISSUE_TRIGGERS:
        if t["product"] == product and t["version"] == current:
            if t["threshold"] < 0 or float(status.get(t["metric"], 0) or 0) >= t["threshold"]:
                risk = max_risk(risk, "medium")
                reasons.append({"level": "medium", "type": "已知问题",
                                "text": f"{t['title']}：{t['detail']}"})
    if not overview["up_to_date"] and not reasons:
        risk = max_risk(risk, "low")
        reasons.append({"level": "low", "type": "版本演进",
                        "text": f"已有新版本 {overview['latest_version']} 可用，包含功能与修复更新"})
    if overview["up_to_date"]:
        reasons.append({"level": "info", "type": "版本演进", "text": "当前已是最新版本，暂无升级必要，保持规则库更新即可"})

    # ---- 发布说明汇总（关键变更点） ----
    key_changes = {"新增功能": [], "安全修复": [], "已知问题修复": [], "优化": [], "说明": [], "EOL 提示": []}
    for rel in overview["releases"]:
        for note in rel["notes"]:
            bucket = key_changes.setdefault(note.get("category", "优化"), [])
            bucket.append(f"[{rel['version']}] {note.get('title', '')}：{note.get('detail', '')}")

    # ---- 升级时机建议 ----
    timing = {
        "interruption": "升级过程中设备需重启、业务中断（官方升级文档明确，云威胁串接等操作亦属高危操作）",
        "window": "建议选择业务低峰窗口执行（如 00:00–06:00），并提前通知相关业务方",
        "ha": "双机（HA）环境务必按官方《双机升级/回退方案》执行：先升级备机并切换主备，再升级主机",
        "prerequisites": [
            "软件升级授权有效（设备控制台【授权管理】确认）",
            "使用 acheck 巡检插件执行『升级前检查』场景巡检",
            "已完成配置备份（可直接调用本 Agent 一键备份）",
            "记录当前版本与license信息，准备对应目标版本升级包",
        ],
    }

    # ---- 行动清单（联动本 Agent 能力；AF 跨架构时拆分为旧架构升级 / 客服迁移 / 新架构升级） ----
    checklist = [
        {"step": 1, "action": "立即执行一次完整配置备份（结构化快照 + 配置文件）", "agent_action": "backup_now"},
        {"step": 2, "action": "确认软件升级授权有效、执行升级前巡检", "agent_action": None},
        {"step": 3, "action": "选择业务低峰窗口，双机环境先备后主", "agent_action": None},
    ]
    if path.get("cross_arch_migration"):
        split_k = kb.version_key(kb.AF_ARCH_SPLIT)
        old_part = [h for h in path["hops"] if kb.version_key(h) < split_k]
        new_part = [h for h in path["hops"] if kb.version_key(h) >= split_k]
        if old_part:
            checklist.append({"step": len(checklist) + 1,
                              "action": f"旧架构内逐级升级：{' → '.join(old_part)}", "agent_action": None})
        checklist.append({"step": len(checklist) + 1,
                          "action": "跨架构升级需联系深信服客服评估，并按官方方案重装系统盘迁移至新架构（不可直接升级覆盖）",
                          "agent_action": None})
        if new_part:
            checklist.append({"step": len(checklist) + 1,
                              "action": f"迁移完成后再逐级升级新架构：{' → '.join(new_part)}", "agent_action": None})
    else:
        checklist.append({"step": len(checklist) + 1,
                          "action": "按路径逐级升级：" + (" → ".join(path["hops"]) if path["hops"] else "无需升级"),
                          "agent_action": None})
    checklist.append({"step": len(checklist) + 1,
                      "action": "升级完成后执行升级后检查，并再次备份形成新基线", "agent_action": "backup_now"})

    return {
        "device_name": device_name,
        "product": product,
        "product_name": overview["product_name"],
        "current_version": current,
        "latest_version": overview["latest_version"],
        "up_to_date": overview["up_to_date"],
        "recommendation": _recommendation(risk),
        "risk": risk,
        "reasons": reasons,
        "upgrade_path": path,
        "key_changes": key_changes,
        "timing": timing,
        "checklist": checklist,
        "data_sources": overview["sources"],
    }


def _recommendation(risk: str) -> str:
    return {
        "high": "强烈建议尽快升级",
        "medium": "建议在近期维护窗口升级",
        "low": "可按常规迭代计划升级",
    }.get(risk, "维持现状，关注版本动态")


def max_risk(a: str, b: str) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return a if order.get(a, 0) >= order.get(b, 0) else b
