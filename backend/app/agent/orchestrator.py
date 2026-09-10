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
    conv_summary_prompt,
)
from app.agent.tools import TOOLS_BY_NAME, get_tools
from app.config import settings
from app.services import personal_kb_service
from app.services.app_settings import get_llm_config

MAX_TOOL_ROUNDS = 8
TOOL_RESULT_LIMIT = 8000
HISTORY_LIMIT = 24
# 记忆提取间隔：至少积累 N 条消息才提取记忆
MEMORY_EXTRACT_INTERVAL = 6


class AgentOrchestrator:
    def __init__(self) -> None:
        self._client = None
        self._client_cfg = None

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
        device = db.get_device(device_id) or {}
        db.add_message(conv_id, "user", {"text": user_message})
        db.touch_conversation(conv_id, title=user_message)
        yield {"type": "meta", "conv_id": conv_id, "device_id": device_id}

        if self._llm() is None:
            async for ev in self._offline_reply(conv_id, user_message, device_id, device):
                yield ev
            return
        async for ev in self._run_llm_loop(conv_id, device_id, device,
                                           user_message=user_message, use_knowledge=use_knowledge):
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

        device = db.get_device(device_id) or {}
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
            guardrails.check_tool_call(action["tool_name"], tool_args, device)   # 执行前复核
            try:
                client = await get_client(device_id)
                result = await tool.handler(client, tool_args, device)
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
            db.update_pending_action(action_id, status="executed",
                                     result_json=json.dumps(result, ensure_ascii=False)[:4000])
            guardrails.audit_tool(action["tool_name"], tool_args, "executed", conv_id, device_id)
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

    def _build_memory_context(self, device_id: str) -> str | None:
        """加载该设备的长期记忆，构建上下文注入消息。"""
        items = db.get_memory_items(device_id, limit=15)
        return memory_injection_message(items)

    async def _extract_memory(self, conv_id: str, device_id: str) -> None:
        """对话结束后提取记忆：生成摘要 + 提取重要事实。"""
        messages = db.get_messages(conv_id)
        if len(messages) < MEMORY_EXTRACT_INTERVAL:
            return
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
            # 1. 生成会话摘要
            summary_resp = await llm.chat.completions.create(
                model=get_llm_config()["model"],
                messages=[
                    {"role": "system", "content": conv_summary_prompt()},
                    {"role": "user", "content": conversation_text[:3000]},
                ],
                temperature=0.3, max_tokens=300,
            )
            summary = summary_resp.choices[0].message.content or ""
            if summary:
                db.save_conv_summary(conv_id, summary.strip(), device_id)

            # 2. 提取重要事实（用户偏好、关键决策等）
            fact_prompt = """从对话中提取重要的事实信息，适合保存到长期记忆中以便后续对话参考。
提取以下类型的信息（如果存在）：
1. 用户偏好或习惯（如：用户喜欢先检查再操作、用户关注安全策略等）
2. 设备的重要配置决策或发现
3. 用户明确表达的观点或要求

如果没有重要信息，回复"无"。
每条信息一行，格式：[类型] 内容"""
            fact_resp = await llm.chat.completions.create(
                model=get_llm_config()["model"],
                messages=[
                    {"role": "system", "content": fact_prompt},
                    {"role": "user", "content": conversation_text[:3000]},
                ],
                temperature=0.3, max_tokens=500,
            )
            fact_text = (fact_resp.choices[0].message.content or "").strip()
            if fact_text and fact_text != "无":
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
        history = db.get_messages(conv_id)[-HISTORY_LIMIT:]
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
        for m in history:
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
                messages.append({"role": "tool", "tool_call_id": content.get("tool_call_id", ""),
                                 "content": content.get("content", "")})
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
        """LLM 兜底技能选择：一次非流式小调用；失败返回 None（回退全量模式）。"""
        try:
            resp = await self._llm().chat.completions.create(
                model=get_llm_config()["model"],
                messages=[
                    {"role": "system", "content": skills.skill_catalog_message(dtype)},
                    {"role": "user", "content": user_message[:500]},
                ],
                temperature=0.0, max_tokens=16,
            )
            return skills.parse_skill_choice(resp.choices[0].message.content or "", dtype)
        except Exception:   # noqa: BLE001 —— 技能选择失败不影响主流程
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

    async def _run_llm_loop(self, conv_id: str, device_id: str, device: dict,
                            user_message: str = "", use_knowledge: bool = False) -> AsyncGenerator[dict, None]:
        messages = self._build_messages(conv_id, device)
        dtype = device.get("type", "")
        # 技能路由：关键词优先（离线可用）→ LLM 按目录兜底 → 未命中回退全量工具模式
        skill = skills.select_skill(user_message, dtype)
        if skill is None and user_message and self._llm() is not None:
            skill = await self._llm_select_skill(user_message, dtype)
        if skill:
            messages.append({"role": "system", "content": skills.skill_guide_message(skill)})
            yield {"type": "skill_selected", "skill": skill.id, "name": skill.name}
        # 知识库检索技能：勾选「查询知识库」后启用（不参与关键词路由）
        if use_knowledge:
            kb = skills.kb_search_skill()
            messages.append({"role": "system", "content": kb.guide})
            yield {"type": "skill_selected", "skill": kb.id, "name": kb.name}
        tool_schemas, tools_by_name = self._tool_scope(skill, dtype, use_knowledge)
        for _ in range(MAX_TOOL_ROUNDS):
            text_parts: list[str] = []
            tool_calls: dict[int, dict] = {}
            try:
                stream = await self._llm().chat.completions.create(
                    model=get_llm_config()["model"], messages=messages, tools=tool_schemas,
                    temperature=settings.llm_temperature, stream=True)
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
                # 对话结束，提取记忆
                await self._extract_memory(conv_id, device_id)
                # 勾选知识库的对话：后台静默沉淀个人知识库词条
                if use_knowledge:
                    personal_kb_service.schedule_sediment(conv_id)
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
                if tool.write:
                    # ---- 写操作：护栏 → 生成变更计划 → 挂起等待确认 ----
                    try:
                        guardrails.check_tool_call(name, args, device)
                        client = await get_client(device_id)
                        plan = await tool.prepare(client, args, device)
                    except guardrails.GuardrailError as e:
                        db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                         "content": f"该操作被安全护栏拦截：{e}"})
                        messages.append({"role": "tool", "tool_call_id": call["id"],
                                         "content": f"该操作被安全护栏拦截：{e}"})
                        yield {"type": "tool_result", "name": name, "preview": f"已拦截：{e}"}
                        continue
                    except Exception as e:   # noqa: BLE001
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

                # ---- 只读工具：直接执行 ----
                try:
                    client = await get_client(device_id)
                    result = await tool.handler(client, args, device)
                    content = json.dumps(result, ensure_ascii=False)[:TOOL_RESULT_LIMIT]
                    db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                     "content": content})
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                    guardrails.audit_tool(name, args, "ok", conv_id, device_id)
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


def _compact_result(name: str, result) -> str:
    """工具结果的紧凑预览（前端事件展示用）。"""
    if isinstance(result, dict):
        if "plan" in result and isinstance(result.get("plan"), dict):
            return f"恢复计划：共 {result['plan'].get('total', 0)} 项变更"
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