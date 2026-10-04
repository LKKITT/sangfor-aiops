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
import re
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app import db
from app.adapters.factory import get_client
from app.agent import guardrails
from app.agent import skills
from app.agent.prompts import (
    SYSTEM_PROMPT,
    device_context_message,
    memory_injection_message,
    memory_extract_prompt,
)
from app.agent.tools import TOOLS_BY_NAME, get_tools
from app.config import settings
from app.services import personal_kb_service
from app.services.app_settings import get_llm_config

MAX_TOOL_ROUNDS = 8
TOOL_RESULT_LIMIT = 8000
HISTORY_LIMIT = 24
# 历史工具结果瘦身：仅最近 N 条工具结果保留全文，更早的截断为开头摘要，
# 避免多轮工具对话后每轮 prompt 膨胀到数万字符拖慢 LLM 推理（上下文由摘要/记忆机制兜底）
RECENT_TOOL_FULL = 3
TOOL_RESULT_KEEP = 200
# 记忆提取间隔：至少积累 N 条消息才提取记忆
MEMORY_EXTRACT_INTERVAL = 6


GLOBAL_DEVICE_ID = "global"   # 全局模式：不绑定单一设备，跨全部深信服/网络设备操作


def load_any_device(device_id: str) -> dict:
    """按 ID 加载设备上下文：nd_ 前缀为网络设备（netdev_devices 表），global 为全局模式
    （合成上下文，含设备数量供提示词使用），其余为深信服设备。"""
    if device_id == GLOBAL_DEVICE_ID:
        return {"id": GLOBAL_DEVICE_ID, "name": "全局（所有设备）", "type": "global",
                "sangfor_count": len(db.list_devices()),
                "netdev_count": len(db.list_netdev_devices())}
    if str(device_id or "").startswith("nd_"):
        return db.get_netdev_device(device_id) or {}
    return db.get_device(device_id) or {}


def device_context_type(device: dict) -> str:
    """设备上下文类型：网络设备返回 'netdev'，全局模式返回 'global'，
    深信服设备返回其 type（af/ac/scp）。"""
    if str(device.get("id", "")) == GLOBAL_DEVICE_ID or device.get("type") == "global":
        return "global"
    if str(device.get("id", "")).startswith("nd_"):
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
                          use_knowledge: bool = False) -> AsyncGenerator[dict, None]:
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
                                           kb_since_id=kb_since, kb_auto=kb_auto):
            yield ev

    # ================= 确认流恢复 =================

    async def resume_confirm(self, conv_id: str, action_id: str, approved: bool,
                             device_id: str, edited: dict | None = None) -> AsyncGenerator[dict, None]:
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
            try:
                batch_targets = (resolve_batch_targets(tool_args)
                                 if tool and tool.needs_device and tool.device_type != "netdev" else [])
                if batch_targets:
                    # 批量写操作：逐台下发（每台独立护栏检查与连接），聚合结果
                    result = await self._execute_write_batch(action["tool_name"], tool_args,
                                                             batch_targets, conv_id)
                else:
                    guardrails.check_tool_call(
                        action["tool_name"], tool_args,
                        None if (tool and tool.device_type == "netdev") else device)
                    # 知识库沉淀/添加设备/网络设备等工具不依赖 REST 设备连接（needs_device=False）
                    client = await get_client(device_id) if (tool and tool.needs_device) else None
                    result = await tool.handler(client, tool_args, device)
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
            db.update_pending_action(action_id, status="failed" if batch_all_failed else "executed",
                                     result_json=json.dumps(result, ensure_ascii=False)[:4000])
            guardrails.audit_tool(action["tool_name"], tool_args,
                                  "failed" if batch_all_failed else "executed", conv_id, device_id)
            # 用户明确要求沉淀（record_to_kb 执行成功）：登记后触发后台提炼
            if action["tool_name"] == skills.KB_RECORD_TOOL_NAME:
                personal_kb_service.schedule_sediment(conv_id)
            db.add_message(conv_id, "tool", {
                "tool_call_id": tool_call_id, "name": action["tool_name"],
                "content": json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_LIMIT]})
            yield {"type": "confirm_result", "action_id": action_id, "approved": True,
                   "result": _compact_result(action["tool_name"], result)}

        if self._llm() is None:
            yield {"type": "done"}
            return
        async for ev in self._run_llm_loop(conv_id, device_id, device):
            yield ev

    # ================= 记忆管理 =================

    def _schedule_memory_extraction(self, conv_id: str, device_id: str) -> None:
        """记忆提取后台化：两次额外 LLM 调用不阻塞 SSE 结束（失败仅记日志）。"""
        async def _run():
            try:
                await self._extract_memory(conv_id, device_id)
            except Exception:   # noqa: BLE001
                import logging
                logging.getLogger("sangfor-agent").warning(
                    "记忆提取失败 conv=%s", conv_id, exc_info=True)

        try:
            asyncio.get_running_loop().create_task(_run())
        except RuntimeError:   # 无事件循环（如同步上下文）时跳过
            pass

    def _build_memory_context(self, device_id: str) -> str | None:
        """加载该设备的长期记忆，构建上下文注入消息。"""
        items = db.get_memory_items(device_id, limit=15)
        return memory_injection_message(items)

    async def _extract_memory(self, conv_id: str, device_id: str) -> None:
        """对话结束后提取记忆：一次 LLM 调用同时生成会话摘要与重要事实。

        增量节流：距上次提取新增消息不足 MEMORY_EXTRACT_INTERVAL 条时跳过，
        避免每轮对话都追加后台 LLM 调用、与下一轮主对话争抢供应商并发。
        """
        messages = db.get_messages(conv_id)
        if len(messages) < MEMORY_EXTRACT_INTERVAL:
            return
        if len(messages) - self._mem_extracted_at.get(conv_id, 0) < MEMORY_EXTRACT_INTERVAL:
            return
        self._mem_extracted_at[conv_id] = len(messages)   # 先占位：连续快速对话不重复提取
        # 获取对话文本
        user_texts = []
        assistant_texts = []
        for m in messages:
            c = m.get("content", {})
            if m["role"] == "user":
                user_texts.append(c.get("text", ""))
            elif m["role"] == "assistant":
                t = c.get("text", "")
                if t and not c.get("tool_calls"):
                    assistant_texts.append(t)
        conversation_text = "用户：" + "\n用户：".join(user_texts[-10:])
        if assistant_texts:
            conversation_text += "\n\n助手：" + "\n助手：".join(assistant_texts[-10:])

        llm = self._llm()
        if llm is None:
            return

        try:
            # 与知识沉淀共用后台串行锁：同一 API Key 并发长调用会互相挤兑超时
            async with personal_kb_service.background_llm_lock():
                resp = await llm.chat.completions.create(
                    model=get_llm_config()["model"],
                    messages=[
                        {"role": "system", "content": memory_extract_prompt()},
                        {"role": "user", "content": conversation_text[:3000]},
                    ],
                    temperature=settings.llm_temperature, top_p=settings.llm_top_p,
                    max_tokens=2048, extra_body=settings.llm_extra_body(),
                )
            summary, fact_text = _parse_memory_extract(resp.choices[0].message.content or "")
            if summary:
                db.save_conv_summary(conv_id, summary, device_id)

            for line in fact_text.split("\n"):
                line = line.strip()
                if not line:
                    continue
                # 解析类型
                category = "fact"
                if "偏好" in line or "习惯" in line:
                    category = "preference"
                elif "操作" in line or "配置" in line or "变更" in line:
                    category = "action_history"
                elif "设备" in line:
                    category = "device_context"
                # 去重：内容相似的不重复保存
                existing = db.get_memory_items(device_id, limit=30)
                if any(line[:30] in item["content"] for item in existing):
                    continue
                db.save_memory_item(device_id, category, line[:300], conv_id)
            # 清理旧记忆
            db.delete_old_memory(device_id, keep=50)
        except Exception:   # noqa: BLE001 —— 记忆提取失败不影响主流程
            import logging
            logging.getLogger("sangfor-agent").warning("记忆提取失败", exc_info=True)

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

    # ================= 技能路由 =================

    async def _llm_select_skill(self, user_message: str, dtype: str):
        """LLM 兜底技能选择：一次非流式小调用；带独立超时（不拖慢首 token），失败回退全量模式。

        仅在关键词路由未命中且消息疑似写操作时触发（见 _run_llm_loop）：
        技能的价值在收窄写工具集与注入流程指引，纯查询直接走全量模式即可。
        """
        try:
            resp = await asyncio.wait_for(self._llm().chat.completions.create(
                model=get_llm_config()["model"],
                messages=[
                    {"role": "system", "content": skills.skill_catalog_message(dtype)},
                    {"role": "user", "content": user_message[:500]},
                ],
                temperature=settings.llm_temperature, top_p=settings.llm_top_p,
                max_tokens=1024, extra_body=settings.llm_extra_body(),
            ), timeout=4.0)
            return skills.parse_skill_choice(resp.choices[0].message.content or "", dtype)
        except (asyncio.TimeoutError, Exception):   # noqa: BLE001 —— 选择失败/超时不影响主流程
            return None

    def _tool_scope(self, skill, dtype: str, use_knowledge: bool) -> tuple[list[dict], dict]:
        """工具注入范围：只读全量 + 技能解锁的写工具；勾选知识库时附加官方知识库工具。"""
        scope = skills.resolve_skill_tools(skill, dtype)
        tools_by_name = {t.name: t for t in scope}
        tool_schemas = [t.schema() for t in scope]
        if use_knowledge and skills.KB_TOOL_NAME not in tools_by_name:
            kb_tool = TOOLS_BY_NAME.get(skills.KB_TOOL_NAME)
            if kb_tool is not None:
                tool_schemas.append(kb_tool.schema())
                tools_by_name[kb_tool.name] = kb_tool
        return tool_schemas, tools_by_name

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
                r = await tool.handler(client, copy.deepcopy(sub_args), d)
                results.append({"device": d["name"], "ok": True, "data": r})
                guardrails.audit_tool(tool_name, tool_args, "ok", conv_id, d["id"])
            except guardrails.GuardrailError as e:
                results.append({"device": d["name"], "ok": False, "error": f"被安全护栏拦截：{e}"})
            except Exception as e:   # noqa: BLE001
                results.append({"device": d["name"], "ok": False, "error": str(e)})
                db.audit("agent.write.failed", {"tool": tool_name, "device": d["name"],
                                                "error": str(e)}, conv_id=conv_id,
                         device_id=d["id"], result="failed")
        for s in missing:
            results.append({"device": s, "ok": False, "error": "未找到匹配的深信服设备"})
        ok_n = sum(1 for r in results if r.get("ok"))
        return {"batch": True, "targets": len(targets), "succeeded": ok_n,
                "results": results,
                "message": f"批量下发完成：成功 {ok_n}/{len(results)} 台"
                           + ("；失败设备请按下方逐台原因处理" if ok_n < len(results) else "")}

    async def _run_llm_loop(self, conv_id: str, device_id: str, device: dict,
                            user_message: str = "", use_knowledge: bool = False,
                            kb_since_id: int | None = None,
                            kb_auto: bool = False) -> AsyncGenerator[dict, None]:
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
        tool_schemas, tools_by_name = self._tool_scope(skill, dtype,
                                                       use_knowledge=use_knowledge or kb_auto)
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
            if final_text:
                yield {"type": "token", "text": final_text}

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
                # 合并内部参数模板（优先从过滤后的工具查找，再回退到全量）
                filtered_tools = get_tools(dtype)
                internal_template = {t.name: getattr(t, "internal", {}) for t in filtered_tools if getattr(t, "internal", None)}
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
                    if (tool.needs_device and tool.device_type != "netdev"
                            and resolve_batch_targets(args)):
                        # 深信服设备类只读工具的批量 fan-out：逐台执行并聚合（网络设备工具自持 devices 解析）
                        result = await self._run_read_batch(tool, args)
                    else:
                        # needs_device=False 的工具（知识库/添加设备/网络设备 SSH 工具）跳过设备登录
                        client = await get_client(device_id) if tool.needs_device else None
                        result = await tool.handler(client, args, device)
                    content = json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_LIMIT]
                    executed_results[call_key] = (content, _compact_result(name, result))
                    db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                     "content": content})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                    guardrails.audit_tool(name, args, "ok", conv_id, device_id)
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

    # ================= 离线兜底（未配置 LLM Key） =================

    async def _offline_reply(self, conv_id: str, message: str, device_id: str,
                             device: dict) -> AsyncGenerator[dict, None]:
        db.audit("agent.offline_chat", {"message": message[:100]},
                 conv_id=conv_id, device_id=device_id)
        text = await self._offline_answer(message, device_id, device)
        db.add_message(conv_id, "assistant", {"text": text, "tool_calls": []})
        for i in range(0, len(text), 80):
            yield {"type": "token", "text": text[i:i + 80]}
        yield {"type": "offline_notice",
               "text": "（未配置 LLM API Key，当前为离线兜底模式：仅支持固定意图查询，"
                       "完整对话能力请在 backend/.env 配置 LLM_API_KEY）"}
        yield {"type": "done"}

    async def _offline_answer(self, message: str, device_id: str, device: dict) -> str:
        from app.services.analyzer import run_checks

        if str(device_id or "").startswith("nd_"):
            return ("我是全局运维助手（当前绑定网络设备）。离线兜底模式仅支持深信服设备的固定意图查询，"
                    "网络设备对话能力需要配置 LLM API Key 后使用（backend/.env 或『平台设置』中的 LLM_API_KEY）。")
        if device_id == GLOBAL_DEVICE_ID:
            return ("我是全局运维助手（全局模式）。离线兜底模式仅支持绑定具体深信服设备后的固定意图查询，"
                    "完整全局对话能力需要配置 LLM API Key 后使用（backend/.env 或『平台设置』中的 LLM_API_KEY）。")
        client = await get_client(device_id)
        if True:
            m = message.lower()
            if re.search(r"状态|健康|cpu|内存|资源", m):
                s = (await client.get_status()).to_dict()
                return (f"**设备状态**（{device.get('name')}）\n- 软件版本：{s['sw_version']}（{s['model']}）\n"
                        f"- CPU：{s['cpu_usage']}%　内存：{s['memory_usage']}%　磁盘：{s['disk_usage']}%\n"
                        f"- 会话：{s['session_count']}/{s['session_capacity']}\n"
                        f"- 运行时间：{s['uptime']}")
            if re.search(r"接口|网口|端口流量", m):
                rows = [i.to_dict() for i in await client.get_interfaces()]
                lines = ["| 接口 | 区域 | IP | 状态 | 收/发 (kbps) |", "|---|---|---|---|---|"]
                lines += [f"| {r['name']} | {r['zone'] or '-'} | {r['ip'] or '-'} | {r['status']} "
                          f"| {r['rx_kbps']}/{r['tx_kbps']} |" for r in rows]
                return "**网络接口**\n" + "\n".join(lines)
            if re.search(r"网络对象|ip组|ip组|地址组|对象", m) and "更新" not in m and "升级" not in m:
                rows = [o.to_dict() for o in await client.get_network_objects()]
                lines = ["| 对象 | 类型 | 成员 | 备注 |", "|---|---|---|---|"]
                lines += [f"| {r['name']} | {r['type']} | {r['members']} | {r['comment'] or '-'} |"
                          for r in rows]
                return "**网络对象**\n" + "\n".join(lines)
            if re.search(r"自定义服务|服务列表", m) or ("服务" in m and "升级" not in m and "更新" not in m):
                rows = [s.to_dict() for s in await client.get_services()]
                lines = ["| 服务 | 协议 | 端口 | 备注 |", "|---|---|---|---|"]
                lines += [f"| {r['name']} | {r['protocol']} | {r['ports']} | {r['comment'] or '-'} |"
                          for r in rows]
                return "**自定义服务**\n" + "\n".join(lines)
            if re.search(r"nat|地址转换", m):
                rules = [n.to_dict() for n in await client.get_nat_rules()]
                lines = ["| ID | 名称 | 类型 | 源 | 目的 | 服务 | 转换 | 启用 | 命中 |",
                         "|---|---|---|---|---|---|---|---|---|"]
                lines += [f"| {r['id']} | {r['name']} | {r['type']} | {r['src_addr']} | {r['dst_addr']} "
                          f"| {r['service']} | {r['translated_addr']}"
                          f"{'：' + r['translated_port'] if r['translated_port'] else ''} "
                          f"| {'✓' if r['enabled'] else '✗'} | {r['hit_count']} |" for r in rules]
                return "**NAT 策略**\n" + "\n".join(lines)
            if re.search(r"acl|访问控制|策略", m):
                rules = [a.to_dict() for a in await client.get_acl_rules()]
                lines = ["| ID | 名称 | 源 | 目的 | 服务 | 动作 | 启用 | 命中 |",
                         "|---|---|---|---|---|---|---|---|"]
                lines += [f"| {r['id']} | {r['name']} | {r['src_addr']} | {r['dst_addr']} | {r['service']} "
                          f"| {r['action']} | {'✓' if r['enabled'] else '✗'} | {r['hit_count']} |"
                          for r in rules]
                return "**访问控制策略**\n" + "\n".join(lines)
            if re.search(r"绑定", m):
                rows = [b.to_dict() for b in await client.get_user_bindings()]
                lines = ["| 用户 | IP | MAC | 类型 | 启用 |", "|---|---|---|---|---|"]
                lines += [f"| {r['user']} | {r['ip']} | {r['mac'] or '-'} | {r['binding_type']} "
                          f"| {'✓' if r['enabled'] else '✗'} |" for r in rows]
                return "**IP-MAC 绑定**\n" + "\n".join(lines)
            if re.search(r"体检|检查|风险|分析", m):
                report = run_checks(await client.snapshot_config(), (await client.get_status()).to_dict())
                lines = [f"**配置体检**：得分 {report['score']}/100（{report['grade']}），"
                         f"高危 {report['counts']['high']} / 中危 {report['counts']['medium']} / "
                         f"低危 {report['counts']['low']}", ""]
                for item in report["items"][:10]:
                    lines.append(f"- 【{item['severity']}】{item['title']} → {item['suggestion']}")
                return "\n".join(lines)
            if re.search(r"备份列表|备份", m):
                rows = db.list_backups(device_id)[:10]
                if not rows:
                    return "尚无备份记录，可对我说\"创建备份\"。"
                lines = ["| 备份 | 标签 | 版本 | 时间 |", "|---|---|---|---|"]
                lines += [f"| {r['id']} | {r['label']} | {r['sw_version']} | {r['created_at']} |"
                          for r in rows]
                return "**备份列表**\n" + "\n".join(lines)
            if re.search(r"升级|更新|新版本", m):
                from app.services import upgrade_advisor
                st = await client.get_status()
                advice = await upgrade_advisor.build_upgrade_advice(st.sw_version, st.to_dict(),
                                                                    device.get("name", ""))
                return (f"**升级建议**：{advice['recommendation']}\n"
                        f"- 当前 {advice['current_version']} → 最新 {advice['latest_version']}\n"
                        f"- 升级路径：{' → '.join(advice['upgrade_path']['hops']) or '无需'}\n"
                        f"- 时机：{advice['timing']['window']}\n"
                        f"- 理由：{'; '.join(r['text'] for r in advice['reasons'])}")
            return ("我是深信服售后技术支持 Agent。当前为**离线兜底模式**，可回答：设备状态 / 接口 / NAT / "
                    "访问控制策略 / 用户绑定 / 配置体检 / 备份列表 / 升级建议。"
                    "配置 backend/.env 或『平台设置』中的 LLM_API_KEY 后即可使用完整自然语言对话（含配置变更与恢复）。")


def _kb_hit_result(result) -> bool:
    """是否已路由到官方知识库并取得返回：答案或澄清反问均算（澄清时助手会结合设备信息作答，
    同样有沉淀价值）；查询失败（status != ok 或无内容）不算。"""
    return (isinstance(result, dict) and result.get("status") == "ok"
            and bool((result.get("answer") or "").strip()))


def _parse_memory_extract(text: str) -> tuple[str, str]:
    """解析合并的记忆提取输出（[摘要]/[事实] 两节），返回 (摘要, 事实文本)。

    格式完全不符时返回空串（本轮放弃提取），与提取失败同等对待，不影响主流程。
    """
    s = text or ""
    summary_m = re.search(r"\[摘要\]\s*(.*?)(?:\[事实\]|\Z)", s, re.S)
    facts_m = re.search(r"\[事实\]\s*(.*)\Z", s, re.S)
    if not (summary_m or facts_m):
        return "", ""
    return (summary_m.group(1).strip() if summary_m else "",
            facts_m.group(1).strip() if facts_m else "")


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