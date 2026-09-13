"""个人知识库服务（LLM WIKI）：把勾选知识库的对话沉淀为结构化词条，并生成反思报告。

- 数据来源：audit_logs 中有 search_official_knowledge 调用记录的对话（即勾选过「查询知识库」）；
- LLM WIKI：用 LLM 把对话提炼为结构化知识词条（主题/分类/摘要/要点/步骤/官方引用/标签），
  按主题去重后落库，随对话积累形成个性化知识网络；
- 反思报告：定期基于词条与统计生成「阶段总结 / 知识盲区 / 待验证结论 / 学习建议」；
- 全程静默降级：未配置 LLM 或提炼失败不影响对话主流程，词条保持待沉淀状态可手动重试。
"""
import asyncio
import html as htmllib
import importlib.util
import json
import logging
import re
import sys
import threading
from pathlib import Path

import httpx
from openai import AsyncOpenAI

from app import db
from app.config import PROJECT_DIR
from app.services.app_settings import get_llm_config
from app.services.update_service import UA, _embedded_content_lines, _strip_html_to_lines

CRAWLER_SCRIPT = PROJECT_DIR / "skills" / "sangfor-support-crawler" / "scripts" / "sangfor_support_crawler.py"
_crawler_mod = None
_crawler_lock = threading.Lock()


def _load_crawler_module():
    """按路径加载 sangfor-support-crawler 技能脚本（复用，不复制代码）。"""
    global _crawler_mod
    if _crawler_mod is not None:
        return _crawler_mod
    with _crawler_lock:
        if _crawler_mod is not None:
            return _crawler_mod
        if not CRAWLER_SCRIPT.exists():
            raise FileNotFoundError(f"案例爬虫技能脚本不存在：{CRAWLER_SCRIPT}")
        spec = importlib.util.spec_from_file_location("sangfor_support_crawler", CRAWLER_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        _crawler_mod = mod
        return mod

log = logging.getLogger("sangfor-agent")

WIKI_SYSTEM_PROMPT = """你是个人知识库管理员，按 LLM-Wiki 方式维护知识：每个词条是一个原子主题的 wiki 页面，
词条之间通过"相关主题"互相链接，同类知识会与已有页面合并而不是重复建页。把「工程师与AI助手的对话」提炼为结构化知识词条，要求：
1. 只提炼有沉淀价值的技术知识（配置方法、故障排查、版本知识、安全策略、最佳实践），忽略寒暄与过程性内容；
2. **原子主题**：一个词条只讲一个独立概念/问题；对话含多个独立主题时拆分为多个词条；
3. **固定骨架**：content_md 按需包含「适用版本 / 现象 / 根因 / 处理步骤 / 验证方法」，让同类词条结构一致、便于比对；
4. **别名**：aliases 给出该主题的常见同义说法 2~4 个（如 ["HA 主备不同步","双机切换异常"]），用于提高检索命中；
5. **交叉引用**：若词条与某已有主题相关，在 related 数组里写已有主题名（如 ["AF双机聚合HA-traffic配置与恢复方法"]）；
6. 忠实于对话内容与引用来源，不得编造对话中没有的信息；
4. 正文精炼：content_md 控制在 300 字内，key_points 最多 5 条，避免超长；
5. 标签用简洁一致的名词（产品优先写 AF/AC，避免「深信服AF」「AF防火墙」「下一代防火墙」等同义变体；每条不超过 4 个）；
6. 只输出 JSON 数组，不要输出任何其他文字或代码块标记。元素格式：
{"topic": "词条主题（简洁名词短语）", "category": "分类（配置方法/故障排查/版本升级/安全策略/最佳实践/其他 之一）", "summary": "一句话摘要", "content_md": "词条正文（markdown，150-300字，按固定骨架组织）", "key_points": ["要点1", "要点2"], "tags": ["标签", "标签"], "aliases": ["同义说法", "别名"], "related": ["相关已有主题名"], "references": [{"title": "引用标题", "url": "官方链接"}]}
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


def _conversation_text(conv_id: str, since_id: int | None = None) -> str:
    """取对话的用户/助手文本与知识库引用（含官方链接），截断保护。

    since_id 给定时只取该消息 id 之后的内容（增量沉淀，不引入之前的会话内容）。
    """
    parts = []
    for m in db.get_messages(conv_id, since_id=since_id):
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


async def generate_entries_for_conv(conv_id: str, force: bool = False,
                                    since_id: int | None = None) -> dict:
    """把对话沉淀为知识词条（按主题去重）。

    since_id 给定时为增量沉淀（只取该消息 id 之后的对话，供勾选后逐次沉淀），
    不受“已沉淀过”跳过限制；全量路径（手动沉淀）仍按会话幂等。
    """
    if db.kb_conv_dismissed(conv_id):
        return {"status": "skipped", "conv_id": conv_id, "reason": "该对话已被移出沉淀队列"}
    if since_id is None and not force and db.conv_kb_sedimented(conv_id):
        return {"status": "skipped", "conv_id": conv_id, "reason": "该对话已沉淀过词条"}
    llm = _llm()
    if llm is None:
        return {"status": "skipped", "conv_id": conv_id, "reason": "未配置 LLM，无法自动提炼词条"}
    text = _conversation_text(conv_id, since_id)
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
    saved, updated = _persist_entries(entries, conv_id)
    log.info("知识库沉淀完成 conv=%s 生成=%s 新增=%s 更新=%s", conv_id, len(entries), saved, updated)
    return {"status": "ok", "conv_id": conv_id,
            "generated": len(entries), "saved": saved, "updated": updated}


def _similar_entry(topic: str) -> tuple[dict | None, float]:
    """按主题检索最相似已有词条；返回 (词条, 相关度)。"""
    rows = db.search_kb_entries(topic, limit=1)
    return (rows[0] if rows else None, float(rows[0].get("score", 0)) if rows else 0.0)


def _persist_entries(entries: list[dict], conv_id: str = "",
                     extra_refs: list[dict] | None = None) -> tuple[int, int]:
    """词条入库（LLM-Wiki 合并式记忆）。返回 (新增数, 更新数)。

    去重合并三级：①精确同主题 → 刷新；②相似主题（检索高分且词元重合）→ 合并入库；
    ③全新主题 → 新建。引用合并顺序：LLM 提炼 → 对话官方链接 → extra_refs。
    """
    saved, updated = 0, 0
    for e in entries:
        topic = (e.get("topic") or "").strip()
        if not topic:
            continue
        refs = _merge_refs(_norm_entry_refs(e.get("references")),
                           _conv_official_refs(conv_id) if conv_id else [])
        refs = _merge_refs(refs, list(extra_refs or []))
        aliases = [str(x)[:20] for x in (e.get("aliases") or [])][:8]
        related = [str(x)[:60] for x in (e.get("related") or [])][:5]
        entry_fields = {
            "summary": (e.get("summary") or "")[:200],
            "content_md": e.get("content_md") or "",
            "key_points": [str(x) for x in (e.get("key_points") or [])][:8],
            "tags": [str(x)[:20] for x in (e.get("tags") or [])][:8],
            "references": refs,
            "aliases": aliases,
        }
        if related:
            see_also = "相关主题：" + "、".join(f"[[{r}]]" for r in related)
            entry_fields["content_md"] = (entry_fields["content_md"]
                                          + chr(10) * 2 + see_also).strip()

        # ① 精确同主题 / ② 相似主题（检索高分 + 主题词元覆盖率 + 产品线一致）→ 合并更新
        existing = db.get_kb_entry_by_topic(topic)
        if existing is None:
            similar, score = _similar_entry(topic)
            topic_tokens = set(db._tokenize_keyword(topic))
            if similar and score >= 12:
                sim_tokens = set(db._tokenize_keyword(similar.get("topic") or ""))
                lat_new = {t for t in topic_tokens if t.isascii()}
                lat_old = {t for t in sim_tokens if t.isascii()}
                lat_ok = not lat_new or not lat_old or bool(lat_new & lat_old)   # 产品线保护：af 与 scp 不合并
                coverage = len(topic_tokens & sim_tokens) / max(1, len(topic_tokens))
                if lat_ok and (score >= 18 or (score >= 12 and coverage >= 0.4)):
                    existing = similar
        if existing:
            # 合并更新：新信息并入已有页面（要点/标签/引用/别名并集去重），保持单一权威页面
            db.update_kb_entry_content(existing["id"], conv_id or existing.get("conv_id", ""),
                                       entry_fields["summary"],
                                       entry_fields["content_md"], entry_fields["key_points"],
                                       entry_fields["references"], entry_fields["tags"],
                                       aliases=entry_fields.get("aliases"))
            updated += 1
            continue
        db.save_kb_entry({
            "conv_id": conv_id, "topic": topic[:80],
            "category": (e.get("category") or "其他")[:20],
            **entry_fields,
        })
        saved += 1
    return saved, updated


def _probe_case_error(mod, crawler_holder: dict, url: str) -> str | None:
    """爬虫取不到案例时，查一次原始 API 拿明确原因（权限/不存在）；非案例链接返回 None。"""
    crawler = crawler_holder.get("crawler")
    if crawler is None:
        return None
    params = mod.SangforSupportCrawler.parse_support_url(url)
    sid = params.get("source_id", "")
    if not sid:
        return None   # 非案例链接（如 productDocument 页面）→ 交回静态抓取
    try:
        resp = crawler.session.get(f"{crawler.API_BASE}/getDetailById/{sid}", timeout=20)
        data = resp.json()
    except Exception:   # noqa: BLE001
        return None
    code = data.get("code")
    msg = str(data.get("msg") or "")
    if code not in (0, 200):
        hint = "该案例可能需要更高的社区权限" + (f"（要求：{msg}）" if msg else "")             if code == 666 else f"平台返回：{msg or f'code={code}'}"
        return f"官方案例读取失败：{hint}；可换用当前账号有权访问的案例链接，或改用内容页链接沉淀"
    if not data.get("rows"):
        return "官方案例不存在或已下架"
    return None


def _crawler_credentials() -> tuple[str, str]:
    """爬虫凭据：界面配置/.env（与诸葛知识库同一社区账号体系）→ zhuge 技能内置账号兜底。"""
    from app.services import zhuge_kb_service
    return zhuge_kb_service._credentials()


async def _ingest_via_crawler(url: str, note: str = "") -> dict | None:
    """用案例爬虫技能抓取 support 链接并沉淀词条。

    返回 dict = 技能路径结论（成功/明确失败）；None = 技能不可用，调用方回退静态抓取。
    """
    try:
        mod = _load_crawler_module()
    except Exception as e:   # noqa: BLE001 —— 技能或依赖缺失
        log.warning("案例爬虫技能不可用：%s", e)
        return None

    user, pwd = _crawler_credentials()
    if not user or not pwd:
        return {"status": "error",
                "reason": "未配置社区账号：请在『平台设置』填写 BBS 社区账号密码后重试"}

    crawler_holder: dict = {}

    def _crawl():
        crawler = mod.SangforSupportCrawler(user, pwd)
        if not crawler.login():
            raise RuntimeError("社区SSO登录失败：请检查『平台设置』中的社区账号密码")
        crawler_holder["crawler"] = crawler
        return crawler.crawl_by_url(url)

    try:
        case = await asyncio.wait_for(asyncio.to_thread(_crawl), timeout=90)
    except asyncio.TimeoutError:
        return {"status": "error", "reason": "案例爬取超时（>90s），请稍后重试"}
    except RuntimeError as e:
        return {"status": "error", "reason": str(e)}
    except Exception as e:   # noqa: BLE001 —— 爬虫异常（网络等）：回退静态
        log.warning("案例爬虫失败，回退静态抓取：%s", e)
        return None
    if case is None:
        # 区分：非案例链接（无 category_id）→ 回退静态；案例读取失败（权限/不存在）→ 明确报错
        err = _probe_case_error(mod, crawler_holder, url)
        if err:
            return {"status": "error", "reason": err}
        return None

    # 案例材料 → LLM WIKI 提炼
    llm = _llm()
    if llm is None:
        return {"status": "skipped", "reason": "未配置 LLM，无法提炼案例内容"}
    NL = chr(10)
    NL2 = NL
    material = (f"案例标题：{case.title}"
                f"{NL}产品：{case.product_name} {case.product_version}"
                f"{NL}适用版本：{case.suite_version or '不限'}"
                f"{NL}分类：{case.category or '不限'}"
                f"{NL}摘要：{case.summary or ''}"
                f"{NL}{NL}案例正文：{NL}{(case.content_text or '')[:8000]}")
    if len(material) < 100:
        return {"status": "error", "reason": "案例内容过少，未能提炼有效知识"}
    resp = await llm.chat.completions.create(
        model=get_llm_config()["model"],
        messages=[{"role": "system", "content": WIKI_SYSTEM_PROMPT},
                  {"role": "user",
                   "content": "来源标题：" + case.title + NL2 + "来源链接：" + (case.url or url)
                              + NL2 + NL2 + "以下是需要提炼的案例内容：" + NL2 + material}],
        temperature=0.2, max_tokens=8000)
    entries = _parse_entries(resp.choices[0].message.content or "")
    if not entries:
        return {"status": "error", "reason": "未能从案例内容提炼出知识词条，请换一个案例链接"}
    case_ref = [{"title": f"官方案例：{case.title}", "url": case.url or url}]
    saved, updated = _persist_entries(entries, conv_id="", extra_refs=case_ref)
    log.info("案例沉淀完成 url=%s 新增=%s 更新=%s", url, saved, updated)
    return {"status": "ok", "title": case.title, "url": case.url or url,
            "generated": len(entries), "saved": saved, "updated": updated, "via": "crawler"}


async def ingest_url(url: str, note: str = "") -> dict:
    """抓取网页内容，经 LLM WIKI 提炼为知识词条沉淀到个人知识库（词条引用附来源链接）。

    support.sangfor.com.cn 链接优先走 sangfor-support-crawler 技能
    （BBS 社区 SSO 认证的案例 OpenAPI，可获取静态抓取拿不到的动态渲染内容），
    技能不可用/非案例链接时回退静态抓取。
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return {"status": "error", "reason": "请提供有效的 http(s) 链接"}
    if "support.sangfor.com.cn" in url:
        crawler_result = await _ingest_via_crawler(url, note)
        if crawler_result is not None:
            return crawler_result   # 技能路径有明确结论
        # None = 技能不可用/链接不适用 → 回退静态抓取
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, verify=False) as cli:
            resp = await cli.get(url, headers={"User-Agent": UA})
    except Exception as e:   # noqa: BLE001 —— 抓取失败降级
        return {"status": "error", "reason": f"链接抓取失败：{e}"}
    if resp.status_code != 200:
        return {"status": "error", "reason": f"链接返回 HTTP {resp.status_code}"}
    raw = resp.text
    tm = re.search(r"<title>([^<]+)</title>", raw)
    title = htmllib.unescape(tm.group(1)).strip()[:120] if tm else url
    # 内容提取：优先页面内嵌数据（support 平台页面形态），不足时回退全文剥标签；
    # 行级清洗去掉导航/菜单短行（SPA 列表页静态 HTML 只有菜单文字）
    text = "\n".join(_embedded_content_lines(raw))
    if len(text) < 200:
        text = "\n".join(_strip_html_to_lines(raw))
    text = "\n".join(l for l in text.splitlines() if len(l.strip()) >= 8)[:8000]
    if len(text) < 150:
        return {"status": "error",
                "reason": "该页面为动态渲染（列表/登录类页面由前端异步加载），未能提取到有效正文；"
                          "请打开具体内容页/详情页后复制其链接再沉淀"}
    llm = _llm()
    if llm is None:
        return {"status": "skipped", "reason": "未配置 LLM，无法提炼页面内容"}
    resp2 = await llm.chat.completions.create(
        model=get_llm_config()["model"],
        messages=[{"role": "system", "content": WIKI_SYSTEM_PROMPT},
                  {"role": "user",
                   "content": f"来源标题：{title}\n来源链接：{url}\n\n以下是需要提炼的网页内容：\n{text}"}],
        temperature=0.2, max_tokens=8000)
    entries = _parse_entries(resp2.choices[0].message.content or "")
    if not entries:
        return {"status": "error",
                "reason": "页面内容未提炼出有价值的知识词条（可能是导航/列表类页面），"
                          "请改用内容更具体的详情页链接"}
    src_ref = [{"title": f"来源：{title}", "url": url}]
    saved, updated = _persist_entries(entries, conv_id="", extra_refs=src_ref)
    log.info("链接沉淀完成 url=%s 新增=%s 更新=%s", url, saved, updated)
    return {"status": "ok", "title": title, "url": url,
            "generated": len(entries), "saved": saved, "updated": updated}


KB_AUDIT_ACTIONS = ("agent.tool.search_official_knowledge", "agent.tool.record_to_kb")


def pending_items() -> list[dict]:
    """待沉淀对话明细：标题/消息数/最后活跃/知识库提问（供前端勾选沉淀）。"""
    conv_ids = db.list_pending_kb_convs()
    if not conv_ids:
        return []
    conv_set = set(conv_ids)
    qa: dict[str, list[str]] = {c: [] for c in conv_ids}
    for a in db.list_audit(limit=1000):
        cid = a.get("conv_id", "")
        if cid in conv_set and a.get("action") in KB_AUDIT_ACTIONS:
            try:
                args = json.loads(a.get("detail_json") or "{}").get("args") or {}
            except (ValueError, TypeError):
                args = {}
            q = str(args.get("question") or args.get("note") or "").strip()
            if q and len(qa[cid]) < 5:
                qa[cid].append(q)
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


def schedule_sediment(conv_id: str, since_id: int | None = None) -> None:
    """对话结束后台静默沉淀（不阻塞、不影响对话主流程）。"""
    async def _run():
        try:
            await generate_entries_for_conv(conv_id, since_id=since_id)
        except Exception:   # noqa: BLE001
            log.warning("个人知识库后台沉淀失败 conv=%s", conv_id, exc_info=True)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:   # 无事件循环（如同步测试环境）时跳过
        pass
