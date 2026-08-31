"""Agent 编排器：LLM 工具循环 + 两阶段变更确认 + SSE 事件流。

状态机（与 LangGraph 同构的轻量自研实现，工具协议 OpenAI function-calling 兼容）：

  用户消息 → [LLM 推理 ⇄ 只读工具执行]（最多 8 轮）
                  │
                  ├─ 命中写操作工具 → guardrails 检查 → prepare 生成变更计划
                  │        → 落库 pending_action → emit confirm_required → 暂停
                  │
  确认卡片 → resume_confirm(approved) → 执行（审计+自动安全备份） → 工具结果回填
                  → [LLM 继续推理] → 最终回答（全程流式）

会话每一步都持久化到 SQLite，暂停/恢复不丢失上下文；未配置 LLM Key 时启用
离线兜底助手（规则意图匹配直接调用工具），保证演示零依赖可跑。
"""
import json
import re
from typing import AsyncGenerator

from openai import AsyncOpenAI

from app import db
from app.adapters.factory import get_client
from app.agent import guardrails
from app.agent.prompts import SYSTEM_PROMPT, device_context_message
from app.agent.tools import TOOLS, TOOLS_BY_NAME, TOOL_SCHEMAS
from app.config import settings
from app.services.app_settings import get_llm_config

MAX_TOOL_ROUNDS = 8
TOOL_RESULT_LIMIT = 8000
HISTORY_LIMIT = 24

# 写操作工具的内部参数模板（_resource/_op 由工具定义注入，调用时合并）
WRITE_TOOL_TEMPLATE = {t.name: getattr(t, "internal", {}) for t in TOOLS if getattr(t, "internal", None)}


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

    async def stream_chat(self, conv_id: str, user_message: str, device_id: str) -> AsyncGenerator[dict, None]:
        guardrails.check_user_request(user_message)   # 黑名单先于 LLM 拦截
        device = db.get_device(device_id) or {}
        db.add_message(conv_id, "user", {"text": user_message})
        db.touch_conversation(conv_id, title=user_message)
        yield {"type": "meta", "conv_id": conv_id, "device_id": device_id}

        if self._llm() is None:
            async for ev in self._offline_reply(conv_id, user_message, device_id, device):
                yield ev
            return
        async for ev in self._run_llm_loop(conv_id, device_id, device):
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

    # ================= LLM 工具循环 =================

    def _build_messages(self, conv_id: str, device: dict) -> list[dict]:
        history = db.get_messages(conv_id)[-HISTORY_LIMIT:]
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        ctx = device_context_message(device, None)
        if ctx:
            messages.append({"role": "system", "content": ctx})
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

    async def _run_llm_loop(self, conv_id: str, device_id: str, device: dict) -> AsyncGenerator[dict, None]:
        messages = self._build_messages(conv_id, device)
        for _ in range(MAX_TOOL_ROUNDS):
            text_parts: list[str] = []
            tool_calls: dict[int, dict] = {}
            try:
                stream = await self._llm().chat.completions.create(
                    model=get_llm_config()["model"], messages=messages, tools=TOOL_SCHEMAS,
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
                tool = TOOLS_BY_NAME.get(name)
                if tool is None:
                    db.add_message(conv_id, "tool", {"tool_call_id": call["id"], "name": name,
                                                     "content": f"未知工具 {name}"})
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": f"未知工具 {name}"})
                    continue
                try:
                    args = json.loads(raw_args or "{}")
                except json.JSONDecodeError:
                    args = {}
                args = {**WRITE_TOOL_TEMPLATE.get(name, {}), **args}

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
                        f"- mbuf：{s['mbuf_usage']}%　会话：{s['session_count']}/{s['session_capacity']}\n"
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
