"""个人知识库服务（LLM WIKI）：把勾选知识库的对话沉淀为结构化词条，并生成反思报告。

- 数据来源：audit_logs 中有 search_official_knowledge 调用记录的对话（即勾选过「查询知识库」）；
- LLM WIKI：用 LLM 把对话提炼为结构化知识词条（主题/分类/摘要/要点/步骤/官方引用/标签），
  按主题去重后落库，随对话积累形成个性化知识网络；
- 反思报告：定期基于词条与统计生成「阶段总结 / 知识盲区 / 待验证结论 / 学习建议」；
- 全程静默降级：未配置 LLM 或提炼失败不影响对话主流程，词条保持待沉淀状态可手动重试。
"""
import asyncio
import json
import logging

from openai import AsyncOpenAI

from app import db
from app.services.app_settings import get_llm_config

log = logging.getLogger("sangfor-agent")

WIKI_SYSTEM_PROMPT = """你是个人知识库管理员。把一段「工程师与AI助手的对话」提炼为结构化知识词条（WIKI），要求：
1. 只提炼有沉淀价值的技术知识（配置方法、故障排查、版本知识、安全策略、最佳实践），忽略寒暄与过程性内容；
2. 每个词条聚焦一个主题；对话含多个独立主题时拆分为多个词条；
3. 忠实于对话内容与引用来源，不得编造对话中没有的信息；
4. 正文精炼：content_md 控制在 300 字内，key_points 最多 5 条，避免超长；
5. 标签用简洁一致的名词（产品优先写 AF/AC，避免「深信服AF」「AF防火墙」「下一代防火墙」等同义变体；每条不超过 4 个）；
6. 只输出 JSON 数组，不要输出任何其他文字或代码块标记。元素格式：
{"topic": "词条主题（简洁名词短语）", "category": "分类（配置方法/故障排查/版本升级/安全策略/最佳实践/其他 之一）", "summary": "一句话摘要", "content_md": "词条正文（markdown，150-300字，步骤用有序列表）", "key_points": ["要点1", "要点2"], "tags": ["标签", "标签"], "references": [{"title": "引用标题", "url": "官方链接"}]}
7. references 引用材料中出现的官方链接时，必须使用对象形式并原样保留 url，不得改写、编造或丢弃链接；材料中没有 url 的引用只用 {"title": "标题"}。
如果对话没有沉淀价值，只输出 []。"""

REFLECTION_SYSTEM_PROMPT = """你是个人知识库的复盘助手。基于工程师的词条编辑历史与统计数据，写一份「反思与总结」报告（markdown），包含四个小节：
## 阶段总结
（沉淀了什么、覆盖哪些领域、数量趋势）
## 知识盲区
（哪些领域提问多但词条少、或明显缺失的主题）
## 待验证结论
（材料中提到但未确认的结论/版本号/参数，列出建议实测验证的点；没有则写"暂无"）
## 学习建议
（下一步建议深入的 2-3 个方向，具体到主题）
只基于给出的材料，不要编造；全文 500 字以内。"""


def _llm() -> AsyncOpenAI | None:
    cfg = get_llm_config()
    if not cfg["api_key"] or cfg["api_key"].startswith("your-"):
        return None
    return AsyncOpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=120)


def _conversation_text(conv_id: str) -> str:
    """取对话的用户/助手文本与知识库引用（含官方链接），截断保护。"""
    parts = []
    for m in db.get_messages(conv_id):
        c = m.get("content", {})
        text = (c.get("text") or "").strip()
        if not text:
            continue
        if m["role"] == "user":
            parts.append(f"用户：{text}")
        elif m["role"] == "assistant" and not c.get("tool_calls"):
            parts.append(f"助手：{text[:1500]}")
        elif m["role"] == "tool":
            # 知识库工具结果：引用条目（含官方超链接）需进入材料，供词条沉淀引用
            try:
                d = json.loads(c.get("content") or "")
            except (ValueError, TypeError):
                continue
            if isinstance(d, dict) and d.get("source") == "zhuge_official_kb":
                parts.append("知识库引用：" +
                             json.dumps(d.get("references") or [], ensure_ascii=False)[:2000])
    return "\n".join(parts)[:8000]


def _top_level_boundaries(text: str) -> list[int]:
    """返回 JSON 文本中所有位于字符串外的 ']' 或 '}' 的下标（升序）。"""
    out, in_str, escape = [], False, False
    for i, ch in enumerate(text):
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str and ch in "]}":
            out.append(i)
    return out


def _parse_entries(raw: str) -> list[dict]:
    """解析 LLM 输出的词条 JSON 数组。

    容忍两类偏差：代码块包裹；输出在 max_tokens 处被截断（推理类模型思考 token
    也计入 max_tokens，正文易被切断）。截断时自动回退到最后一个完整的词条对象，
    丢弃残尾并闭合数组，保证已生成部分不丢失。
    """
    if not raw:
        return []
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip()
        if text.lower().startswith("json"):
            text = text.split("\n", 1)[-1]
    start = text.find("[")
    if start < 0:
        return []
    candidate = text[start:]
    # 优先尝试原样解析（截掉数组后可能存在的杂尾）
    end = candidate.rfind("]")
    attempts = [candidate[:end + 1]] if end >= 0 else []
    # 截断修复：从最后一个顶层边界向前回退，'{' 边界补 ']' 闭合数组
    for pos in reversed(_top_level_boundaries(candidate)):
        piece = candidate[:pos + 1]
        if candidate[pos] == "}":
            piece += "\n]"
        attempts.append(piece)
    for attempt in attempts:
        try:
            data = json.loads(attempt)
        except (ValueError, TypeError):
            continue
        if isinstance(data, list):
            return [e for e in data if isinstance(e, dict) and (e.get("topic") or "").strip()]
    return []


def _norm_entry_refs(raw_refs) -> list[dict]:
    """词条引用规整：统一为 {title, url?} 对象，url 必须是合法链接。"""
    out = []
    for r in raw_refs or []:
        if isinstance(r, dict):
            title = str(r.get("title") or "")[:150]
            url = str(r.get("url") or "").strip()
            if url.startswith(("http://", "https://")):
                out.append({"title": title or url, "url": url[:500]})
            elif title:
                out.append({"title": title})
        else:
            s = str(r).strip()
            if s.startswith(("http://", "https://")):
                out.append({"title": s, "url": s[:500]})
            elif s:
                out.append({"title": s[:150]})
        if len(out) >= 8:
            break
    return out


def _conv_official_refs(conv_id: str) -> list[dict]:
    """从对话的知识库工具结果中收集带 url 的官方引用（代码级保底，不依赖 LLM 搬运链接）。"""
    out, seen = [], set()
    for m in db.get_messages(conv_id):
        if m["role"] != "tool":
            continue
        try:
            d = json.loads(m.get("content", {}).get("content") or "")
        except (ValueError, TypeError):
            continue
        if not isinstance(d, dict) or d.get("source") != "zhuge_official_kb":
            continue
        for r in _norm_entry_refs(d.get("references")):
            if r.get("url") and r["url"] not in seen:
                seen.add(r["url"])
                out.append(r)
    return out


def _merge_refs(llm_refs: list[dict], official_refs: list[dict]) -> list[dict]:
    """LLM 给出的引用在前，对话中出现过的官方链接补录在后（按 url 去重）。"""
    out = list(llm_refs)
    seen = {r["url"] for r in out if r.get("url")}
    for r in official_refs:
        if r["url"] not in seen:
            seen.add(r["url"])
            out.append(r)
    return out[:8]


async def generate_entries_for_conv(conv_id: str, force: bool = False) -> dict:
    """把一个对话沉淀为知识词条（按主题去重）。"""
    if db.kb_conv_dismissed(conv_id):
        return {"status": "skipped", "conv_id": conv_id, "reason": "该对话已被移出沉淀队列"}
    if not force and db.conv_kb_sedimented(conv_id):
        return {"status": "skipped", "conv_id": conv_id, "reason": "该对话已沉淀过词条"}
    llm = _llm()
    if llm is None:
        return {"status": "skipped", "conv_id": conv_id, "reason": "未配置 LLM，无法自动提炼词条"}
    text = _conversation_text(conv_id)
    if len(text) < 50:
        return {"status": "skipped", "conv_id": conv_id, "reason": "对话内容过少，无沉淀价值"}
    resp = await llm.chat.completions.create(
        model=get_llm_config()["model"],
        messages=[{"role": "system", "content": WIKI_SYSTEM_PROMPT},
                  {"role": "user", "content": text}],
        temperature=0.2, max_tokens=8000)
    raw = resp.choices[0].message.content or ""
    finish = (resp.choices[0].finish_reason or "")
    if finish == "length":
        log.warning("知识库提炼输出被截断(conv=%s)，使用截断修复解析", conv_id)
    entries = _parse_entries(raw)
    saved, skipped = 0, 0
    for e in entries:
        topic = (e.get("topic") or "").strip()
        if db.kb_entry_topic_exists(topic):
            skipped += 1
            continue
        db.save_kb_entry({
            "conv_id": conv_id, "topic": topic[:80],
            "category": (e.get("category") or "其他")[:20],
            "summary": (e.get("summary") or "")[:200],
            "content_md": e.get("content_md") or "",
            "key_points": [str(x) for x in (e.get("key_points") or [])][:8],
            "tags": [str(x)[:20] for x in (e.get("tags") or [])][:8],
            "references": _merge_refs(_norm_entry_refs(e.get("references")),
                                      _conv_official_refs(conv_id)),
        })
        saved += 1
    log.info("知识库沉淀完成 conv=%s 生成=%s 保存=%s 去重=%s", conv_id, len(entries), saved, skipped)
    return {"status": "ok", "conv_id": conv_id,
            "generated": len(entries), "saved": saved, "dedup_skipped": skipped}


def pending_items() -> list[dict]:
    """待沉淀对话明细：标题/消息数/最后活跃/知识库提问（供前端勾选沉淀）。"""
    conv_ids = db.list_pending_kb_convs()
    if not conv_ids:
        return []
    conv_set = set(conv_ids)
    qa: dict[str, list[str]] = {c: [] for c in conv_ids}
    for a in db.list_audit(limit=1000):
        cid = a.get("conv_id", "")
        if cid in conv_set and a.get("action") == "agent.tool.search_official_knowledge":
            try:
                q = (json.loads(a.get("detail_json") or "{}").get("args") or {}).get("question")
            except (ValueError, TypeError):
                q = None
            if q and len(qa[cid]) < 5:
                qa[cid].append(str(q))
    items = []
    for conv_id in conv_ids:
        conv = db.get_conversation(conv_id) or {}
        items.append({
            "conv_id": conv_id,
            "title": (conv.get("title") or "(无标题)")[:60],
            "updated_at": conv.get("updated_at", ""),
            "msg_count": len(db.get_messages(conv_id)),
            "kb_questions": qa.get(conv_id, []),
        })
    return items


async def process_pending(limit: int = 5, convs: list[str] | None = None) -> dict:
    """批量沉淀：指定 convs 时只处理这些对话（忽略已移出队列的），否则按队列顺序处理。"""
    if convs:
        targets = [c for c in convs if not db.kb_conv_dismissed(c)]
    else:
        targets = db.list_pending_kb_convs()[:max(1, limit)]
    results = []
    for conv_id in targets:
        try:
            results.append(await generate_entries_for_conv(conv_id))
        except Exception as e:   # noqa: BLE001 —— 单个对话失败不阻塞批次
            results.append({"status": "error", "conv_id": conv_id, "reason": str(e)})
    return {"processed": len(results),
            "saved": sum(r.get("saved", 0) for r in results if r.get("status") == "ok"),
            "results": results, "remaining": len(db.list_pending_kb_convs())}


def _filter_entries_by_range(entries: list[dict], start: str, end: str) -> list[dict]:
    """按沉淀日期过滤词条（created_at 前 10 位为 YYYY-MM-DD，字符串比较即日期比较）。"""
    out = []
    for e in entries:
        day = (e.get("created_at") or "")[:10]
        if start and day < start:
            continue
        if end and day > end:
            continue
        out.append(e)
    return out


async def generate_reflection(start: str = "", end: str = "") -> dict:
    """基于词条与统计生成反思与总结报告；支持自定义时间节点（按沉淀日期过滤）。"""
    llm = _llm()
    if llm is None:
        return {"status": "skipped", "reason": "未配置 LLM，无法生成反思报告"}
    entries = _filter_entries_by_range(db.list_kb_entries(limit=500), start, end)
    if not entries:
        return {"status": "skipped", "reason": "所选时间范围内没有知识词条"}
    # 时间范围内的分组统计（与全局 stats 同口径：标签分组去重）
    categories: dict[str, int] = {}
    timeline: dict[str, int] = {}
    for e in entries:
        categories[e["category"]] = categories.get(e["category"], 0) + 1
        day = (e.get("created_at") or "")[:10]
        timeline[day] = timeline.get(day, 0) + 1
    scoped_stats = {
        "total": len(entries),
        "categories": [{"name": k, "value": v} for k, v in
                       sorted(categories.items(), key=lambda x: -x[1])],
        "tags": db.group_tags(entries, top_n=15),
        "timeline": [{"day": d, "value": v} for d, v in sorted(timeline.items())],
        "range": {"start": start or "最早", "end": end or db.now()[:10]},
    }
    material = json.dumps({
        "统计时间范围": f"{start or '最早'} 至 {end or db.now()[:10]}",
        "stats": {k: scoped_stats[k] for k in ("total", "categories", "tags", "timeline")},
        "entries": [{"topic": e["topic"], "category": e["category"], "summary": e["summary"],
                     "tags": e["tags"], "created_at": e["created_at"]} for e in entries],
    }, ensure_ascii=False)
    resp = await llm.chat.completions.create(
        model=get_llm_config()["model"],
        messages=[{"role": "system", "content": REFLECTION_SYSTEM_PROMPT},
                  {"role": "user", "content": material[:6000]}],
        temperature=0.3, max_tokens=2500)
    content = (resp.choices[0].message.content or "").strip()
    if not content:
        return {"status": "skipped", "reason": "模型未返回内容，请重试"}
    period = f"{start or '最早'} ~ {end or db.now()[:10]}"
    rec = db.save_kb_reflection(content, scoped_stats, period=period)
    return {"status": "ok", "reflection": rec}


def schedule_sediment(conv_id: str) -> None:
    """对话结束后台静默沉淀（不阻塞、不影响对话主流程）。"""
    async def _run():
        try:
            await generate_entries_for_conv(conv_id)
        except Exception:   # noqa: BLE001
            log.warning("个人知识库后台沉淀失败 conv=%s", conv_id, exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:   # 无事件循环（如同步测试环境）时跳过
        pass
