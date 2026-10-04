"""Agent 记忆管理：会话摘要生成、长期事实提取与设备记忆注入。

从 orchestrator 拆出的独立模块。orch 实参即 AgentOrchestrator 实例：
需要访问其 LLM 客户端（orch._llm()）与增量节流状态（orch._mem_extracted_at）。
"""
import asyncio
import logging

from app import db
from app.agent.prompts import memory_extract_prompt, memory_injection_message
from app.config import settings
from app.services import personal_kb_service
from app.services.app_settings import get_llm_config

# 记忆提取间隔：至少积累 N 条消息才提取记忆
MEMORY_EXTRACT_INTERVAL = 6

log = logging.getLogger("sangfor-agent")


def parse_memory_extract(text: str) -> tuple[str, str]:
    """解析合并的记忆提取输出（[摘要]/[事实] 两节），返回 (摘要, 事实文本)。

    格式完全不符时返回空串（本轮放弃提取），与提取失败同等对待，不影响主流程。
    """
    import re
    s = text or ""
    summary_m = re.search(r"\[摘要\]\s*(.*?)(?:\[事实\]|\Z)", s, re.S)
    facts_m = re.search(r"\[事实\]\s*(.*)\Z", s, re.S)
    if not (summary_m or facts_m):
        return "", ""
    return (summary_m.group(1).strip() if summary_m else "",
            facts_m.group(1).strip() if facts_m else "")


def build_memory_context(device_id: str) -> str | None:
    """加载该设备的长期记忆，构建上下文注入消息。"""
    items = db.get_memory_items(device_id, limit=15)
    return memory_injection_message(items)


def schedule_memory_extraction(orch, conv_id: str, device_id: str) -> None:
    """记忆提取后台化：额外 LLM 调用不阻塞 SSE 结束（失败仅记日志）。"""
    async def _run():
        try:
            await extract_memory(orch, conv_id, device_id)
        except Exception:   # noqa: BLE001
            log.warning("记忆提取失败 conv=%s", conv_id, exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:   # 无事件循环（如同步上下文）时跳过
        pass


async def extract_memory(orch, conv_id: str, device_id: str) -> None:
    """对话结束后提取记忆：一次 LLM 调用同时生成会话摘要与重要事实。

    增量节流：距上次提取新增消息不足 MEMORY_EXTRACT_INTERVAL 条时跳过，
    避免每轮对话都追加后台 LLM 调用、与下一轮主对话争抢供应商并发。
    """
    messages = db.get_messages(conv_id)
    if len(messages) < MEMORY_EXTRACT_INTERVAL:
        return
    if len(messages) - orch._mem_extracted_at.get(conv_id, 0) < MEMORY_EXTRACT_INTERVAL:
        return
    if len(orch._mem_extracted_at) > 500:   # 防慢性增长：截断最旧的一半会话记录
        for k in sorted(orch._mem_extracted_at)[:250]:
            orch._mem_extracted_at.pop(k, None)
    orch._mem_extracted_at[conv_id] = len(messages)   # 先占位：连续快速对话不重复提取
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

    llm = orch._llm()
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
        summary, fact_text = parse_memory_extract(resp.choices[0].message.content or "")
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
        log.warning("记忆提取失败", exc_info=True)
