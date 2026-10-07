"""Agent 编排器：LLM 工具循环 + 两阶段变更确认 + SSE 事件流 + 记忆管理。

状态机（与 LangGraph 同构的轻量自研实现，工具协议 OpenAI function-calling 兼容）：

  用户消息 → [LLM 推理 ⇄ 只读工具执行]（最多 8 轮）
                  │
                  ├─ 命中写操作工具 → guardrails 检查 → prepare 生成变更计划
                  │        → 落库 pending_action → emit confirm_required → 暂停
                  │
  确认卡片 → resume_confirm(approved) → 执行（审计+自动安全备份） → 工具结果回填
                  → [LLM 继续推理] → 最终回答（全程流式）

记忆管理：
  - 对话开始时注入该设备的长期记忆（用户偏好、操作记录等）
  - 对话结束后自动生成摘要保存到短期记忆
  - 重要事实自动提取并保存到长期记忆
"""
import asyncio
import copy
import json
import time
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app import db
from app.adapters.factory import get_client
from app.agent import guardrails
from app.agent import memory, offline, routing, skills
from app.agent.prompts import SYSTEM_PROMPT, device_context_message
from app.agent.tools import TOOLS_BY_NAME, get_tools
from app.config import settings
from app.services import config_service
from app.services import personal_kb_service
from app.services import device_scope
from app.services.device_cache import device_cache
from app.services.app_settings import get_llm_config

MAX_TOOL_ROUNDS = 8
# 工具结果回传上限：网络设备批量查询（几十台 × 健康四命令）轻松超过 8000，
# 截断会让 LLM 拿到残缺数据而漏答设备；放宽到 24K（历史瘦身机制另行控制 prompt 体积）
TOOL_RESULT_LIMIT = 24000
# 只读工具整轮并发上限：一轮内全部为只读调用时并发执行（延迟从"各工具之和"降为"最慢者"）。
# 设备侧单 token 并发上限未知，取保守值；写操作永远不参与并行。
READ_CONCURRENCY = 3
HISTORY_LIMIT = 24
# 历史工具结果瘦身：仅最近 N 条工具结果保留全文，更早的截断为开头摘要，
# 避免多轮工具对话后每轮 prompt 膨胀到数万字符拖慢 LLM 推理（上下文由摘要/记忆机制兜底）
RECENT_TOOL_FULL = 3
TOOL_RESULT_KEEP = 200

GLOBAL_DEVICE_ID = device_scope.GLOBAL_DEVICE_ID   # 全局模式：不绑定单一设备，跨全部深信服/网络设备操作


def load_any_device(device_id: str) -> dict:
    """按 ID 加载设备上下文：nd_ 前缀为网络设备（netdev_devices 表），global 为全局模式
    （合成上下文，含设备数量供提示词使用），其余为深信服设备。"""
    if device_id == GLOBAL_DEVICE_ID:
        return {"id": GLOBAL_DEVICE_ID, "name": "全局（所有设备）", "type": "global",
                "sangfor_count": len(db.list_devices()),
                "netdev_count": len(db.list_netdev_devices())}
    if device_scope.device_kind(device_id) == "netdev":
        return db.get_netdev_device(device_id) or {}
    return db.get_device(device_id) or {}


def device_context_type(device: dict) -> str:
    """设备上下文类型：网络设备返回 'netdev'，全局模式返回 'global'，
    深信服设备返回其 type（af/ac/scp）。"""
    if str(device.get("id", "")) == GLOBAL_DEVICE_ID or device.get("type") == "global":
        return "global"
    if device_scope.device_kind(device.get("id", "")) == "netdev":
        return "netdev"
    return device.get("type", "")


# devices 参数中表示"全部设备"的写法
ALL_DEVICE_TOKENS = {"all", "*", "全部", "所有", "所有设备", "全部设备"}


def resolve_batch_targets(args: dict) -> list[str]:
    """提取工具参数中的 devices 批量目标（设备名称/IP/ID/all 列表）。"""
    raw = args.get("devices")
    if not raw:
        return []
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in raw if str(x).strip()]


def _global_target_guidance(devices: list[dict]) -> str:
    """全局模式下调用深信服设备类工具且未指定 devices 时的引导文案（回填给 LLM）。"""
    if not devices:
        return ("当前为全局模式且尚未添加深信服设备。网络设备可用 netdev_* 工具的 devices "
                "参数指定后操作；也可以引导用户先在「设备管理」页添加深信服设备。")
    names = "、".join(d["name"] for d in devices)
    return ("当前为全局模式（未绑定单一设备），存在多台深信服设备，调用深信服设备类工具时"
            "必须用 devices 参数指定目标设备（可传 [\"all\"] 表示全部深信服设备）。"
            f"可选设备：{names}。若用户未指明是对哪台设备操作，请先询问用户，"
            "或建议其在设备选择器中切换到具体设备；网络设备直接用 netdev_* 工具操作。")


def match_sangfor_devices(targets: list[str]) -> tuple[list[dict], list[str]]:
    """按名称/IP/管理地址/ID 匹配深信服设备（devices 表）；支持 all/全部 表示全部设备。
    返回 (匹配设备, 未匹配项)。"""
    devices = db.list_devices()
    matched, missing = [], []
    for s in targets:
        if s.lower() in ALL_DEVICE_TOKENS:
            if not devices:
                missing.append(f"{s}（尚无深信服设备）")
                continue
            for d in devices:
                if d not in matched:
                    matched.append(d)
            continue
        hit = next((d for d in devices
                    if d["id"] == s or d["name"] == s
                    or (d.get("base_url") or "") == s
                    or (d.get("base_url") or "").rstrip("/").endswith(s)
                    or (d["name"] or "").lower() == s.lower()), None)
        if hit is None:
            subs = [d for d in devices if s.lower() in (d["name"] or "").lower()]
            hit = subs[0] if len(subs) == 1 else None
            if hit is None and len(subs) > 1:
                missing.append(f"{s}（匹配到多台，请用全名）")
                continue
        if hit:
            if hit not in matched:
                matched.append(hit)
        else:
            missing.append(s)
    return matched, missing


class AgentOrchestrator:
    def __init__(self) -> None:
        self._client = None
        self._client_cfg = None
        # 会话 → 上次记忆提取时的消息数（增量节流，避免每轮都跑后台提取）
        self._mem_extracted_at: dict[str, int] = {}

    def _llm(self) -> AsyncOpenAI | None:
        """按当前配置（界面平台设置 > .env）返回 LLM 客户端；配置变化时自动重建。"""
        cfg = get_llm_config()
        if not cfg["api_key"] or cfg["api_key"].startswith("your-"):
            return None
        key = (cfg["base_url"], cfg["api_key"], cfg["model"])
        if self._client is None or self._client_cfg != key:
            self._client = AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=120)
            self._client_cfg = key
        return self._client

    # ================= 对话入口 =================

    async def stream_chat(self, conv_id: str, user_message: str, device_id: str,
                          use_knowledge: bool = False,
                          mcps: list[str] | None = None,
                          skill_folders: list[str] | None = None) -> AsyncGenerator[dict, None]:
        guardrails.check_user_request(user_message)   # 黑名单先于 LLM 拦截
        device = load_any_device(device_id)
        # 本轮消息起点：知识沉淀（命中官方知识库时）只取本轮新增对话，不引入之前会话内容
        since_id = db.max_message_id(conv_id)
        db.add_message(conv_id, "user", {"text": user_message})
        # 写入设备归属：供前端切换菜单后按设备恢复最近会话
        db.touch_conversation(conv_id, title=user_message, device_id=device_id)
        kb_since = since_id
        # 知识问答意图：未勾选时对功能/故障类提问自动启用分层检索（本地个人知识库优先 → 官方兜底）
        kb_auto = (not use_knowledge) and bool(user_message) and skills.is_kb_intent(user_message)
        yield {"type": "meta", "conv_id": conv_id, "device_id": device_id}

        if self._llm() is None:
            async for ev in self._offline_reply(conv_id, user_message, device_id, device):
                yield ev
            return
        async for ev in self._run_llm_loop(conv_id, device_id, device,
                                           user_message=user_message, use_knowledge=use_knowledge,
                                           kb_since_id=kb_since, kb_auto=kb_auto,
                                           mcps=mcps, skill_folders=skill_folders):
            yield ev

    # ================= 确认流恢复 =================

    async def resume_confirm(self, conv_id: str, action_id: str, approved: bool,
                             device_id: str, edited: dict | None = None,
                             mcps: list[str] | None = None,
                             skill_folders: list[str] | None = None) -> AsyncGenerator[dict, None]:
        action = db.get_pending_action(action_id)
        if not action or action["conv_id"] != conv_id:
            yield {"type": "error", "text": "确认任务不存在"}
            return
        if action["status"] != "pending":
            yield {"type": "error", "text": f"该变更已被处理（{action['status']}），请勿重复操作"}
            return

        device = load_any_device(device_id)
        # 全局模式确认：单台深信服工具执行回退到动作所属会话绑定的设备（避免对 "global" 建连）
        if device_id == GLOBAL_DEVICE_ID:
            conv_device_id = (db.get_conversation(action["conv_id"]) or {}).get("device_id", "")
            if conv_device_id and conv_device_id != GLOBAL_DEVICE_ID:
                device_id = conv_device_id
                device = load_any_device(device_id)
        payload = json.loads(action["args_json"])
        tool_call_id, tool_args = payload.get("tool_call_id", ""), payload.get("args", {})
        # 用户在确认卡片上编辑过的参数（仅允许覆盖 data 内的业务字段，绝不改资源/操作类型）
        if edited and isinstance(edited.get("data"), dict):
            allowed = set(tool_args.get("data") or {}) | set(edited["data"].keys())
            tool_args = {**tool_args,
                         "data": {**(tool_args.get("data") or {}),
                                  **{k: v for k, v in edited["data"].items() if k in allowed}}}
            payload["args"] = tool_args
            db.update_pending_action(action_id, args_json=json.dumps(payload, ensure_ascii=False))
        tool = TOOLS_BY_NAME.get(action["tool_name"])

        if not approved:
            db.update_pending_action(action_id, status="rejected")
            db.audit("agent.write.rejected", {"tool": action["tool_name"], "args": tool_args},
                     conv_id=conv_id, device_id=device_id, actor="user", result="rejected")
            db.add_message(conv_id, "tool", {"tool_call_id": tool_call_id, "name": action["tool_name"],
                                             "content": "用户在界面上审阅变更计划后拒绝执行。"})
            yield {"type": "confirm_result", "action_id": action_id, "approved": False}
        else:
            write_duration_ms = 0.0
            try:
                batch_targets = (resolve_batch_targets(tool_args)
                                 if tool and tool.needs_device and tool.device_type != "netdev" else [])
                if batch_targets:
                    # 批量写操作：逐台下发（每台独立护栏检查与连接），聚合结果
                    _tb0 = time.perf_counter()
                    result = await self._execute_write_batch(action["tool_name"], tool_args,
                                                             batch_targets, conv_id)
                    write_duration_ms = (time.perf_counter() - _tb0) * 1000
                else:
                    guardrails.check_tool_call(
                        action["tool_name"], tool_args,
                        None if (tool and tool.device_type == "netdev") else device)
                    # 变更前自动安全备份（深信服设备写操作）：失败即中止执行，保证始终可回退；
                    # 知识库沉淀/添加设备/网络设备等工具不依赖 REST 设备连接（needs_device=False）
                    safety_backup_id = ""
                    if tool and tool.needs_device and tool.device_type != "netdev":
                        rec = await config_service.create_backup(
                            device_id, label=f"变更前自动备份 · {action['tool_name']}",
                            kind="pre_change", created_by="agent")
                        safety_backup_id = rec["id"]
                    client = await get_client(device_id) if (tool and tool.needs_device) else None
                    _t0 = time.perf_counter()
                    result = await tool.handler(client, tool_args, device)
                    write_duration_ms = (time.perf_counter() - _t0) * 1000
                batch_all_failed = isinstance(result, dict) and result.get("batch") \
                    and not result.get("succeeded")
            except guardrails.GuardrailError as e:
                db.update_pending_action(action_id, status="blocked")
                db.add_message(conv_id, "tool", {"tool_call_id": tool_call_id, "name": action["tool_name"],
                                                 "content": f"变更被安全护栏拦截：{e}"})
                yield {"type": "error", "text": str(e)}
                return
            except Exception as e:   # noqa: BLE001 —— 设备执行失败回填给 LLM 说明
                err = f"设备执行失败：{e}"
                db.update_pending_action(action_id, status="failed",
                                         result_json=json.dumps({"error": str(e)}, ensure_ascii=False))
                db.audit("agent.write.failed", {"tool": action["tool_name"], "error": str(e)},
                         conv_id=conv_id, device_id=device_id, result="failed")
                db.add_message(conv_id, "tool", {"tool_call_id": tool_call_id, "name": action["tool_name"],
                                                 "content": err})
                yield {"type": "confirm_result", "action_id": action_id, "approved": True, "failed": True}
                return
            device_cache.invalidate(device_id)   # 配置已变更：可视化缓存失效（含批量在 _execute_write_batch 内逐台失效）
            db.update_pending_action(action_id, status="failed" if batch_all_failed else "executed",
                                     result_json=json.dumps(result, ensure_ascii=False)[:4000])
            guardrails.audit_tool(action["tool_name"], tool_args,
                                  "failed" if batch_all_failed else "executed", conv_id, device_id,
                                  duration_ms=write_duration_ms)
            # 用户明确要求沉淀（record_to_kb 执行成功）：登记后触发后台提炼
            if action["tool_name"] == skills.KB_RECORD_TOOL_NAME:
                personal_kb_service.schedule_sediment(conv_id)
            db.add_message(conv_id, "tool", {
                "tool_call_id": tool_call_id, "name": action["tool_name"],
                "content": json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_LIMIT]})
            yield {"type": "confirm_result", "action_id": action_id, "approved": True,
                   "result": _compact_result(action["tool_name"], result),
                   "safety_backup_id": safety_backup_id}

        if self._llm() is None:
            yield {"type": "done"}
            return
        async for ev in self._run_llm_loop(conv_id, device_id, device, mcps=mcps, skill_folders=skill_folders):
            yield ev

    # ================= 记忆管理（实现见 agent/memory.py） =================

    def _schedule_memory_extraction(self, conv_id: str, device_id: str) -> None:
        memory.schedule_memory_extraction(self, conv_id, device_id)

    def _build_memory_context(self, device_id: str) -> str | None:
        return memory.build_memory_context(device_id)

    async def _extract_memory(self, conv_id: str, device_id: str) -> None:
        await memory.extract_memory(self, conv_id, device_id)

    # ================= LLM 工具循环 =================

    def _build_messages(self, conv_id: str, device: dict) -> list[dict]:
        history = db.get_messages(conv_id, limit=HISTORY_LIMIT)
        # 仅最近 N 条工具结果保留全文，更早的截断，控制每轮 prompt 体积（首字延迟随轮次衰减的主因）
        tool_positions = [i for i, m in enumerate(history) if m["role"] == "tool"]
        full_tool_pos = set(tool_positions[-RECENT_TOOL_FULL:])
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        # 注入设备上下文
        ctx = device_context_message(device, None)
        if ctx:
            messages.append({"role": "system", "content": ctx})
        # 注入长期记忆
        memory_ctx = self._build_memory_context(device.get("id", ""))
        if memory_ctx:
            messages.append({"role": "system", "content": memory_ctx})
        # 注入历史对话摘要（短期记忆）
        conv_summary = db.get_conv_summary(conv_id)
        if conv_summary and conv_summary.get("summary"):
            messages.append({"role": "system",
                             "content": f"## 当前会话摘要\n{conv_summary['summary']}"})
        # 注入待确认卡片上下文（用户续接对话时，LLM 能感知待处理的变更内容）
        pending_actions = self._get_pending_actions_context(conv_id)
        if pending_actions:
            messages.append({"role": "system", "content": pending_actions})
        for i, m in enumerate(history):
            content = m["content"]
            if m["role"] == "user":
                messages.append({"role": "user", "content": content.get("text", "")})
            elif m["role"] == "assistant":
                entry: dict = {"role": "assistant", "content": content.get("text") or ""}
                if content.get("tool_calls"):
                    entry["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                        for tc in content["tool_calls"]]
                if entry["content"] or entry.get("tool_calls"):
                    messages.append(entry)
            elif m["role"] == "tool":
                tool_content = content.get("content", "")
                if i not in full_tool_pos and len(tool_content) > TOOL_RESULT_KEEP:
                    tool_content = (tool_content[:TOOL_RESULT_KEEP]
                                    + "…（历史工具结果已省略，需要时请重新调用工具获取）")
                messages.append({"role": "tool", "tool_call_id": content.get("tool_call_id", ""),
                                 "content": tool_content})
        return messages

    def _get_pending_actions_context(self, conv_id: str) -> str | None:
        """构建待确认动作的上下文，供 LLM 理解用户对当前卡片的修改意图。"""
        pending = db.get_pending_action_by_conv(conv_id)
        if not pending:
            return None
        args = json.loads(pending.get("args_json", "{}"))
        tool_args = args.get("args", {})
        tool_name = pending.get("tool_name", "")
        summary = pending.get("summary", "")
        parts = [
            "## 当前有待确认的变更（以下为用户尚未确认的待操作变更卡片内容）",
            f"待确认操作：{summary}",
            f"工具：{tool_name}",
            f"操作参数：{json.dumps(tool_args, ensure_ascii=False, indent=2)}",
            "用户接下来可能针对此卡片做出修改指示。如果用户要求修改参数，请分析工具参数并调整，",
            "然后调用相应的 update 工具生成新的变更计划。",
        ]
        return "\n".join(parts)

    # ================= 技能路由（实现见 agent/routing.py） =================

    async def _llm_select_skill(self, user_message: str, dtype: str):
        return await routing.llm_select_skill(self, user_message, dtype)

    async def _tool_scope(self, skill, dtype: str, use_knowledge: bool,
                         mcps: list[str] | None = None,
                         skill_folders: list[str] | None = None) -> tuple[list[dict], dict]:
        return await routing.tool_scope(skill, dtype, use_knowledge,
                                        mcps=mcps, skill_folders=skill_folders)

    # ================= 跨设备批量（devices 参数 fan-out） =================

    async def _run_read_batch(self, tool, args: dict) -> dict:
        """深信服设备类只读工具的批量执行：逐台连接并调用 handler，聚合结果。"""
        targets = resolve_batch_targets(args)
        matched, missing = match_sangfor_devices(targets)
        sub_args = {k: v for k, v in args.items() if k != "devices"}
        results = []
        for d in matched:
            try:
                client = await get_client(d["id"])
                r = await tool.handler(client, copy.deepcopy(sub_args), d)
                results.append({"device": d["name"], "ok": True, "data": r})
            except Exception as e:   # noqa: BLE001 —— 单台失败不阻塞同批其他设备
                results.append({"device": d["name"], "ok": False, "error": str(e)})
        for s in missing:
            results.append({"device": s, "ok": False, "error": "未找到匹配的深信服设备"})
        ok_n = sum(1 for r in results if r.get("ok"))
        return {"batch": True, "targets": len(targets), "succeeded": ok_n,
                "results": results,
                "message": f"批量查询完成：成功 {ok_n}/{len(results)} 台"
                           f"（{', '.join(r['device'] for r in results)}），"
                           "请按设备分节汇总回答"}

    def _merge_batch_plan(self, plans: list[dict]) -> dict:
        """把每台设备的变更计划合并为一张确认卡片（首台计划承载 before/after 主体）。"""
        base = dict(plans[0])
        base["title"] = f"{plans[0].get('title', '')}（共 {len(plans)} 台设备）"
        base["batch_devices"] = [{"device": p.get("device", ""), "title": p.get("title", ""),
                                  "warning": p.get("warning", ""),
                                  "detail": p.get("detail", "")} for p in plans]
        warnings: list[str] = []
        for p in plans:
            for w in (p.get("warning") or "").split("\n"):
                w = w.strip()
                if w and w not in warnings:
                    warnings.append(w)
        base["warning"] = "\n".join(warnings)
        return base

    async def _prepare_write_plan(self, tool, name: str, args: dict, device: dict,
                                  device_id: str) -> dict:
        """写操作变更计划生成：单台直通；devices 批量时逐台生成并合并为一张卡片。"""
        targets = (resolve_batch_targets(args)
                   if tool.needs_device and tool.device_type != "netdev" else [])
        if not targets:
            # 网络设备写工具无 REST 客户端，绑定设备只读语义不适用于目标为网络设备的场景
            guardrails.check_tool_call(name, args,
                                       None if tool.device_type == "netdev" else device)
            client = await get_client(device_id) if tool.needs_device else None
            return await tool.prepare(client, args, device)
        matched, missing = match_sangfor_devices(targets)
        if missing:
            raise RuntimeError(f"未找到匹配的深信服设备：{'、'.join(missing)}")
        if not matched:
            raise RuntimeError("未提供有效的目标设备")
        plans = []
        for d in matched:
            sub_args = {k: v for k, v in args.items() if k != "devices"}
            try:
                guardrails.check_tool_call(name, args, d)
                client = await get_client(d["id"])
                plan = await tool.prepare(client, copy.deepcopy(sub_args), d)
            except guardrails.GuardrailError as e:
                raise guardrails.GuardrailError(f"设备「{d['name']}」：{e}") from e
            except Exception as e:   # noqa: BLE001
                raise RuntimeError(f"设备「{d['name']}」生成变更计划失败：{e}") from e
            if isinstance(plan, dict) and plan.get("error"):
                raise RuntimeError(f"设备「{d['name']}」：{plan['error']}")
            plan["device"] = d["name"]
            plans.append(plan)
        return self._merge_batch_plan(plans)

    async def _execute_write_batch(self, tool_name: str, tool_args: dict,
                                   targets: list[str], conv_id: str) -> dict:
        """确认后的批量写执行：逐台下发（护栏/连接/执行相互独立），聚合结果。"""
        tool = TOOLS_BY_NAME[tool_name]
        matched, missing = match_sangfor_devices(targets)
        sub_args = {k: v for k, v in tool_args.items() if k != "devices"}
        results = []
        for d in matched:
            try:
                guardrails.check_tool_call(tool_name, tool_args, d)
                client = await get_client(d["id"])
                _t0 = time.perf_counter()
                r = await tool.handler(client, copy.deepcopy(sub_args), d)
                guardrails.audit_tool(tool_name, tool_args, "ok", conv_id, d["id"],
                                      duration_ms=(time.perf_counter() - _t0) * 1000)
                results.append({"device": d["name"], "ok": True, "data": r})
            except guardrails.GuardrailError as e:
                results.append({"device": d["name"], "ok": False, "error": f"被安全护栏拦截：{e}"})
            except Exception as e:   # noqa: BLE001
                results.append({"device": d["name"], "ok": False, "error": str(e)})
                db.audit("agent.write.failed", {"tool": tool_name, "device": d["name"],
                                                "error": str(e)}, conv_id=conv_id,
                         device_id=d["id"], result="failed")
        for s in missing:
            results.append({"device": s, "ok": False, "error": "未找到匹配的深信服设备"})
        for d in matched:
            device_cache.invalidate(d["id"])   # 每台配置可能已变更：缓存逐台失效
        ok_n = sum(1 for r in results if r.get("ok"))
        return {"batch": True, "targets": len(targets), "succeeded": ok_n,
                "results": results,
                "message": f"批量下发完成：成功 {ok_n}/{len(results)} 台"
                           + ("；失败设备请按下方逐台原因处理" if ok_n < len(results) else "")}

    async def _run_llm_loop(self, conv_id: str, device_id: str, device: dict,
                            user_message: str = "", use_knowledge: bool = False,
                            kb_since_id: int | None = None,
                            kb_auto: bool = False,
                            mcps: list[str] | None = None,
                            skill_folders: list[str] | None = None) -> AsyncGenerator[dict, None]:
        messages = self._build_messages(conv_id, device)
        dtype = device_context_type(device)
        # 技能路由：关键词优先（零延迟、离线可用）→ 疑似写操作时 LLM 按目录兜底 → 回退全量工具模式
        skill = skills.select_skill(user_message, dtype)
        if (skill is None and user_message and self._llm() is not None
                and skills.looks_like_write_intent(user_message)):
            skill = await self._llm_select_skill(user_message, dtype)
        if skill:
            messages.append({"role": "system", "content": skills.skill_guide_message(skill)})
            yield {"type": "skill_selected", "skill": skill.id, "name": skill.name}
        # 知识库检索技能：勾选「查询知识库」后启用（不参与关键词路由）
        if use_knowledge:
            kb = skills.kb_search_skill()
            messages.append({"role": "system", "content": kb.guide})
            yield {"type": "skill_selected", "skill": kb.id, "name": kb.name}
        elif kb_auto:
            # 未勾选但命中知识问答意图：本地个人知识库优先、官方知识库兜底（不自动沉淀）
            messages.append({"role": "system", "content": skills.kb_auto_guide()})
            yield {"type": "skill_selected", "skill": "kb-auto", "name": "知识问答（本地优先）"}
        tool_schemas, tools_by_name = await self._tool_scope(skill, dtype,
                                                             use_knowledge=use_knowledge or kb_auto,
                                                             mcps=mcps, skill_folders=skill_folders)
        # 已启用 Agent Skills 清单注入（对话级选用：skills=None 全部启用，列表=指定项）
        try:
            from app.agent.ext_tools import skills_catalog_message
            catalog = skills_catalog_message(skill_folders)
            if catalog:
                messages.append({"role": "system", "content": catalog})
        except Exception:   # noqa: BLE001
            pass
        # internal 参数模板整轮不变：构建一次（原先在 per-call 循环内重复构建全量表 + dict 推导）
        internal_template = {t.name: getattr(t, "internal", {}) for t in get_tools(dtype)
                             if getattr(t, "internal", None)}
        executed_results: dict = {}   # (工具名, 参数) -> 结果：同一提问内重复调用直接合并
        failed_write_tools: set = set()   # 写工具失败后终止同工具重试
        kb_hit = False   # 本轮是否实际命中官方知识库（返回了答案）：决定是否后台沉淀
        for _ in range(MAX_TOOL_ROUNDS):
            text_parts: list[str] = []
            tool_calls: dict[int, dict] = {}
            try:
                stream = await self._llm().chat.completions.create(
                    model=get_llm_config()["model"], messages=messages, tools=tool_schemas,
                    temperature=settings.llm_temperature, top_p=settings.llm_top_p,
                    stream=True, extra_body=settings.llm_extra_body(stream=True))
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta is None:
                        continue
                    if delta.content:
                        text_parts.append(delta.content)
                        yield {"type": "token", "text": delta.content}
                    for tc in delta.tool_calls or []:
                        slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["arguments"] += tc.function.arguments
            except Exception as e:   # noqa: BLE001 —— LLM/网络错误兜底
                yield {"type": "error", "text": f"模型调用失败：{e}"}
                yield {"type": "done"}
                return

            final_text = "".join(text_parts).strip()
            if not tool_calls:
                db.add_message(conv_id, "assistant", {"text": final_text, "tool_calls": []})
                # 记忆提取与知识库沉淀均后台执行：SSE 立即结束，不产生"答完卡尾"
                self._schedule_memory_extraction(conv_id, device_id)
                if kb_hit:   # 命中官方知识库才自动沉淀，且只沉淀本轮问答（不含之前的会话）
                    personal_kb_service.schedule_sediment(conv_id, since_id=kb_since_id)
                yield {"type": "done"}
                return

            # 有工具调用：先持久化 assistant 消息
            calls = [tool_calls[i] for i in sorted(tool_calls)]
            for i, c in enumerate(calls):
                c["id"] = c["id"] or f"call_{i}_{db.new_id()}"
            db.add_message(conv_id, "assistant", {
                "text": final_text, "tool_calls": [{"id": c["id"], "name": c["name"],
                                                    "arguments": c["arguments"]} for c in calls]})
            # 注：增量 token 已在上方流式下发，这里不再重发整段 final_text——
            # 前端与企微渠道均按事件累加文本，重发会造成每轮回答整体重复。

            # ---- 只读整轮快路径：一轮内全部为可执行只读调用时并发执行 ----
            # 写操作 / 未知工具 / 全局模式缺 devices 引导的轮次不进入（走下方串行路径，确认流零改动）。
            # 本方法是 async generator，SSE 事件只能在生成器本体产出：
            # 结构上先纯解析分类 → 并发执行收齐结果 → 再按原调用顺序落库/产出事件。
            reads: list[dict] = []   # {call, tool, args, key, dup}
            fast = bool(calls)
            seen_keys: set = set()
            for call in calls:
                tool = tools_by_name.get(call["name"])
                if tool is None or tool.write:
                    fast = False
                    break
                try:
                    args = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                args = {**internal_template.get(call["name"], {}), **args}
                if (dtype == "global" and tool.needs_device and tool.device_type != "netdev"
                        and not resolve_batch_targets(args)):
                    fast = False
                    break
                key = (call["name"], json.dumps(args, sort_keys=True, ensure_ascii=False))
                dup = key in executed_results or key in seen_keys   # 跨轮/同轮重复：不执行，复用结果
                seen_keys.add(key)
                reads.append({"call": call, "tool": tool, "args": args, "key": key, "dup": dup})

            if fast:
                for r in reads:
                    yield {"type": "tool_call", "name": r["call"]["name"],
                           "args": {k: v for k, v in r["args"].items() if not str(k).startswith("_")}}
                sem = asyncio.Semaphore(READ_CONCURRENCY)

                async def _exec_read(tool, args, sem):
                    async with sem:
                        _t0 = time.perf_counter()
                        try:
                            if (tool.needs_device and tool.device_type != "netdev"
                                    and resolve_batch_targets(args)):
                                result = await self._run_read_batch(tool, args)
                            else:
                                client = await get_client(device_id) if tool.needs_device else None
                                result = await tool.handler(client, args, device)
                            return (time.perf_counter() - _t0, result)
                        except Exception as e:   # noqa: BLE001
                            return (time.perf_counter() - _t0,
                                    {"_read_error": f"工具执行失败：{e}"})

                unique = [r for r in reads if not r["dup"]]
                outcomes = await asyncio.gather(*(_exec_read(r["tool"], r["args"], sem) for r in unique))
                raws: dict = {}
                dur_by_key: dict = {}
                for r, (dur, result) in zip(unique, outcomes, strict=True):
                    dur_by_key[r["key"]] = dur
                    if isinstance(result, dict) and "_read_error" in result:
                        continue
                    raws[r["key"]] = result
                    raw_content = json.dumps(result, ensure_ascii=False)
                    content = (raw_content if len(raw_content) <= TOOL_RESULT_LIMIT
                               else raw_content[:TOOL_RESULT_LIMIT]
                               + f"…〔结果超长已截断：原文 {len(raw_content)} 字符，"
                                 "请用更细的过滤条件缩小范围后重查〕")
                    executed_results[r["key"]] = (content, _compact_result(r["call"]["name"], result))
                # 失败原因按 key 收集：同 key 重复调用直接复用，不重试执行
                errors = {r["key"]: result["_read_error"]
                          for r, (_dur, result) in zip(unique, outcomes, strict=True)
                          if isinstance(result, dict) and "_read_error" in result}
                for r in reads:
                    call, key, name = r["call"], r["key"], r["call"]["name"]
                    if key in errors:
                        err = errors[key]
                        db.add_message(conv_id, "tool", {"tool_call_id": call["id"],
                                                         "name": name, "content": err})
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": err})
                        yield {"type": "tool_result", "name": name, "preview": err}
                        continue
                    content, preview = executed_results[key]
                    db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                     "content": content})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                    if r["dup"]:   # 重复调用：复用结果，并向模型注入防循环提示
                        # 坑：提示原先只发前端预览，模型每轮看到的仍是原样结果，
                        # 会原样重试到轮次上限（AC 绑定查询曾连续空转 7 轮无答复）。
                        content = (content + "\n〔重复调用已合并：结果与上次相同，请勿原样重试——"
                                   "请基于已有结果作答，或调整参数 / 向用户说明需要补充信息〕")
                        db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                         "content": content})
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                        yield {"type": "tool_result", "name": name,
                               "preview": f"（重复调用已合并，请直接使用已有结果）{preview}"}
                        continue
                    guardrails.audit_tool(name, r["args"], "ok", conv_id, device_id,
                                          duration_ms=dur_by_key.get(key, 0) * 1000)
                    if name == skills.KB_TOOL_NAME and _kb_hit_result(raws.get(key)):
                        kb_hit = True
                        # 标记官方知识库实际命中：待沉淀队列与自动沉淀均以此为准（未命中不入队）
                        db.audit("agent.kb.hit",
                                 {"args": {"question": str(r["args"].get("question", ""))[:200]}},
                                 conv_id=conv_id, device_id=device_id)
                    yield {"type": "tool_result", "name": name, "preview": preview}
                continue

            for call in calls:
                name, raw_args = call["name"], call["arguments"]
                tool = tools_by_name.get(name)
                if tool is None:
                    # 不回退全量表：技能模式下未加载的工具如实告知 LLM，保证写权限收窄生效
                    hint = f"工具 {name} 不存在或不在当前技能可用范围内，请改用其他方式或如实告知用户"
                    db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                     "content": hint})
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": hint})
                    continue
                try:
                    args = json.loads(raw_args or "{}")
                except json.JSONDecodeError:
                    args = {}
                # 合并内部参数模板（模板已在工具轮次循环外构建一次）
                args = {**internal_template.get(name, {}), **args}

                yield {"type": "tool_call", "name": name,
                       "args": {k: v for k, v in args.items() if not str(k).startswith("_")}}
                # 全局模式：深信服设备类工具必须指定 devices 目标（仅一台设备时自动定向，
                # 多台时回填设备清单引导 LLM 明确目标，不消耗写工具重试禁令）
                if (dtype == "global" and tool.needs_device and tool.device_type != "netdev"
                        and not resolve_batch_targets(args)):
                    sangfor = db.list_devices()
                    if len(sangfor) == 1:
                        args = {**args, "devices": [sangfor[0]["name"]]}
                    else:
                        guidance = _global_target_guidance(sangfor)
                        db.add_message(conv_id, "tool", {"tool_call_id": call["id"],
                                                         "name": name, "content": guidance})
                        messages.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": guidance})
                        yield {"type": "tool_result", "name": name,
                               "preview": "全局模式需指定目标设备（见设备清单）"}
                        continue
                if tool.write:
                    call_key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                    if call_key in executed_results:
                        hint = ("该写操作已生成过相同参数的变更计划（见确认卡片），"
                                "请等待用户确认，不要重复发起")
                        db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                         "content": hint})
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": hint})
                        yield {"type": "tool_result", "name": name, "preview": "已合并重复的写操作"}
                        continue
                    # ---- 写操作：护栏 → 生成变更计划 → 挂起等待确认 ----
                    if name in failed_write_tools:
                        hint = (f"工具 {name} 刚才执行失败（原因见上方工具结果），请勿再次重试；"
                                "请基于失败原因向用户说明情况")
                        db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                         "content": hint})
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": hint})
                        yield {"type": "tool_result", "name": name, "preview": "已终止重复重试"}
                        continue
                    try:
                        # 单台直通；devices 批量（深信服设备类工具）在 _prepare_write_plan 内逐台生成并合并
                        plan = await self._prepare_write_plan(tool, name, args, device, device_id)
                    except guardrails.GuardrailError as e:
                        db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                         "content": f"该操作被安全护栏拦截：{e}"})
                        messages.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": f"该操作被安全护栏拦截：{e}"})
                        yield {"type": "tool_result", "name": name, "preview": f"已拦截：{e}"}
                        continue
                    except Exception as e:   # noqa: BLE001
                        failed_write_tools.add(name)   # 防止 LLM 变着参数无限重试
                        err = f"生成变更计划失败：{e}"
                        db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                         "content": err})
                        messages.append({"role": "tool", "tool_call_id": call["id"], "content": err})
                        yield {"type": "tool_result", "name": name, "preview": err}
                        continue
                    action = db.create_pending_action({
                        "id": db.new_id("act_"), "conv_id": conv_id, "tool_name": name,
                        "args_json": json.dumps({"tool_call_id": call["id"], "args": args},
                                                ensure_ascii=False),
                        "summary": plan.get("title", name), "status": "pending",
                        "created_at": db.now()})
                    plan["action_id"] = action["id"]
                    plan["tool_name"] = name
                    yield {"type": "confirm_required", "action": plan}
                    return   # 暂停对话，等待用户在确认卡片操作

                # ---- 只读工具：直接执行（同参数重复调用直接复用结果，防重复并行与数据截断放大） ----
                call_key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                if call_key in executed_results:
                    content, preview = executed_results[call_key]
                    db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                     "content": content})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                    yield {"type": "tool_result", "name": name,
                           "preview": f"（重复调用已合并，请直接使用已有结果）{preview}"}
                    continue
                try:
                    _t0 = time.perf_counter()
                    if (tool.needs_device and tool.device_type != "netdev"
                            and resolve_batch_targets(args)):
                        # 深信服设备类只读工具的批量 fan-out：逐台执行并聚合（网络设备工具自持 devices 解析）
                        result = await self._run_read_batch(tool, args)
                    else:
                        # needs_device=False 的工具（知识库/添加设备/网络设备 SSH 工具）跳过设备登录
                        client = await get_client(device_id) if tool.needs_device else None
                        result = await tool.handler(client, args, device)
                    read_duration_ms = (time.perf_counter() - _t0) * 1000
                    raw_content = json.dumps(result, ensure_ascii=False)
                    content = (raw_content if len(raw_content) <= TOOL_RESULT_LIMIT
                               else raw_content[:TOOL_RESULT_LIMIT]
                               + f"…〔结果超长已截断：原文 {len(raw_content)} 字符，"
                                 "请用更细的过滤条件缩小范围后重查〕")
                    executed_results[call_key] = (content, _compact_result(name, result))
                    db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                     "content": content})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                    guardrails.audit_tool(name, args, "ok", conv_id, device_id,
                                          duration_ms=read_duration_ms)
                    if name == skills.KB_TOOL_NAME and _kb_hit_result(result):
                        kb_hit = True
                        # 标记官方知识库实际命中：待沉淀队列与自动沉淀均以此为准（未命中不入队）
                        db.audit("agent.kb.hit",
                                 {"args": {"question": str(args.get("question", ""))[:200]}},
                                 conv_id=conv_id, device_id=device_id)
                    yield {"type": "tool_result", "name": name,
                           "preview": _compact_result(name, result)}
                except Exception as e:   # noqa: BLE001
                    err = f"工具执行失败：{e}"
                    db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                     "content": err})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": err})
                    yield {"type": "tool_result", "name": name, "preview": err}

        yield {"type": "error", "text": f"已达单次对话最大工具轮次（{MAX_TOOL_ROUNDS}），请拆分问题后重试"}
        yield {"type": "done"}

    # ================= 离线兜底（实现见 agent/offline.py） =================

    async def _offline_reply(self, conv_id: str, message: str, device_id: str,
                             device: dict) -> AsyncGenerator[dict, None]:
        async for ev in offline.offline_reply(conv_id, message, device_id, device):
            yield ev

    async def _offline_answer(self, message: str, device_id: str, device: dict) -> str:
        return await offline.offline_answer(message, device_id, device)


def _kb_hit_result(result) -> bool:
    """是否已路由到官方知识库并取得返回：答案或澄清反问均算（澄清时助手会结合设备信息作答，
    同样有沉淀价值）；查询失败（status != ok 或无内容）不算。"""
    return (isinstance(result, dict) and result.get("status") == "ok"
            and bool((result.get("answer") or "").strip()))


# 兼容旧引用（tests 从本模块导入 _parse_memory_extract）
_parse_memory_extract = memory.parse_memory_extract


def _compact_result(name: str, result) -> str:
    """工具结果的紧凑预览（前端事件展示用）。"""
    if isinstance(result, dict):
        if "plan" in result and isinstance(result.get("plan"), dict):
            return f"恢复计划：共 {result['plan'].get('total', 0)} 项变更"
        if "saved" in result and "updated" in result:
            return (f"提炼 {result.get('generated', '?')} 条词条："
                    f"新增 {result['saved']} / 更新 {result['updated']}")
        if "summary_text" in result:
            return str(result["summary_text"])
        if "_llm_summary" in result:
            return str(result["_llm_summary"])
        if "message" in result:
            return str(result["message"])
        if "当前版本" in result:
            return f"当前版本 {result.get('当前版本')}，最新版本 {result.get('最新版本')}"
    if isinstance(result, list):
        return f"返回 {len(result)} 条记录"
    return "完成"
