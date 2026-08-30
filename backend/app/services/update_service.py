"""软件更新信息抓取服务：多源适配 + 本地缓存 + 来源标注。

来源优先级（官方平台正文需客户/伙伴认证，按可用性自动降级）：
1. official_platform —— support.sangfor.com.cn（配置了登录 Cookie 时抓取，需认证内容否则标记 unavailable）
2. psirt —— www.sangfor.com.cn/sec_center 安全公告（公开，详情页可直接抓取）
3. builtin —— 内置版本知识库快照（保证离线演示完整可用）

所有落库缓存均带 source 与 fetched_at；发布说明按
新增功能 / 安全修复 / 已知问题修复 / 优化 四类归类（关键词规则，确定性）。
"""
import re
from datetime import datetime

import httpx

from app import db
from app.config import settings
from app.services.knowledge import versions as kb

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SangforSupportAgent/1.0"

# support.sangfor.com.cn 关键栏目（调研确认的 URL 模式）
OFFICIAL_URLS = {
    "af": {
        "release_notes": "https://support.sangfor.com.cn/productDocument/read?product_id=13&version_id=1197&category_id=360973",
        "software_list": "https://support.sangfor.com.cn/productSoftware/list?product_id=13",
    },
    "ac": {
        "release_notes": "https://support.sangfor.com.cn/productDocument/read?product_id=22&version_id=73&category_id=97674",
        "software_list": "https://support.sangfor.com.cn/productSoftware/list?product_id=22",
    },
}
SEC_CENTER_URL = "https://www.sangfor.com.cn/sec_center/bulletins"

CLASSIFY_RULES = [
    ("安全修复", re.compile(r"漏洞|CVE-|安全通告|PSIRT|安全修复|安全补丁|入侵", re.I)),
    ("已知问题修复", re.compile(r"修复|解决|已知问题|回滚|缺陷")),
    ("新增功能", re.compile(r"新增|支持|引入|上线")),
    ("优化", re.compile(r"优化|提升|改进|增强|稳定性")),
]


def classify_note(title: str, detail: str = "") -> str:
    text = f"{title} {detail}"
    for label, pattern in CLASSIFY_RULES:
        if pattern.search(text):
            return label
    return "优化"


def _classify_release_notes(notes: list[dict]) -> list[dict]:
    for n in notes:
        raw = n.get("type")
        if raw in ("新增", "优化", "修复", "安全", "说明", "EOL"):
            n["category"] = raw
        else:
            n["category"] = classify_note(n.get("title", ""), n.get("detail", ""))
        # 统一归桶：修复→已知问题修复；安全→安全修复；修复类含安全关键词归入安全修复
        if n["category"] == "修复":
            n["category"] = "安全修复" if CLASSIFY_RULES[0][1].search(
                n.get("title", "") + n.get("detail", "")) else "已知问题修复"
        n["category"] = {"新增": "新增功能", "安全": "安全修复", "说明": "说明", "EOL": "EOL 提示"}.get(
            n["category"], n["category"])
    return notes


# ---------------- 官方平台抓取器 ----------------

async def fetch_official_release_notes(product: str) -> dict:
    """抓取官方平台『新版本发布信息』页。SPA+认证保护：无 Cookie 或正文未渲染时返回 unavailable。"""
    url = OFFICIAL_URLS.get(product, {}).get("release_notes", "")
    if not url:
        return {"source": "official_platform", "status": "unavailable", "payload": None,
                "reason": "未知产品线"}
    headers = {"User-Agent": UA}
    if settings.support_cookie:
        headers["Cookie"] = settings.support_cookie
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as cli:
            resp = await cli.get(url, headers=headers)
        html = resp.text
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}")
        if "请您先认证身份" in html or "{{" in html[:5000]:
            return {"source": "official_platform", "status": "auth_required", "payload": None,
                    "reason": "该栏目正文仅对深信服客户/伙伴/员工开放；可在 .env 配置 SANGFOR_SUPPORT_COOKIE 后重试"}
        rows = _extract_note_items(html)
        if not rows:
            return {"source": "official_platform", "status": "parse_empty", "payload": None,
                    "reason": "页面为动态渲染，未解析到发布说明条目"}
        return {"source": "official_platform", "status": "ok", "payload": rows, "fetched_at": db.now()}
    except Exception as e:   # noqa: BLE001 —— 抓取失败一律降级，不阻塞主流程
        return {"source": "official_platform", "status": "error", "payload": None, "reason": str(e)}


def _extract_note_items(html: str) -> list[dict]:
    out = []
    for m in re.finditer(r"【(新增|优化|修复|安全)】([^<\n]{4,80})", html):
        out.append({"type": m.group(1), "title": m.group(2).strip()})
    return out


# ---------------- PSIRT 公告抓取器（公开） ----------------

async def fetch_psirt_bulletins() -> dict:
    """抓取官网安全中心公告列表/详情。列表页 JS 动态加载，尽力解析；失败降级内置数据。"""
    headers = {"User-Agent": UA}
    advisories: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as cli:
            resp = await cli.get(SEC_CENTER_URL, headers=headers)
            ids = re.findall(r"/sec_center/details/([0-9a-f]{32})", resp.text)
            for hid in list(dict.fromkeys(ids))[:10]:
                detail = await cli.get(f"https://www.sangfor.com.cn/sec_center/details/{hid}", headers=headers)
                parsed = _parse_psirt_detail(detail.text)
                if parsed:
                    advisories.append(parsed)
    except Exception:   # noqa: BLE001 —— 公网抓取失败降级
        advisories = []
    if advisories:
        return {"source": "psirt", "status": "ok", "payload": advisories, "fetched_at": db.now()}
    return {"source": "psirt", "status": "degraded", "payload": None,
            "reason": "安全中心列表为动态渲染，本次未抓到公告；使用内置公告快照"}


def _parse_psirt_detail(html: str) -> dict | None:
    title = re.search(r"<title>([^<]+)</title>", html)
    cvss = re.search(r"CVSS[^0-9]{0,20}(10\.0|\d\.\d)", html)
    affected = re.findall(r"[AV]?(?:AC|AF|SG)?\s*V?(\d+\.\d+\.\d+)", html)
    if not title:
        return None
    return {"id": title.group(1)[:60], "title": title.group(1).strip(),
            "cvss": float(cvss.group(1)) if cvss else None,
            "affected_versions": sorted(set(affected)),
            "source_url": "sec_center/detail"}


# ---------------- 汇总入口 ----------------

async def refresh_update_cache(product: str = "af") -> dict:
    """手动/定时刷新：抓取官方平台与 PSIRT，成功则落缓存。"""
    official = await fetch_official_release_notes(product)
    psirt = await fetch_psirt_bulletins()
    if official["status"] == "ok":
        db.save_update_cache(product, "release_notes", official["payload"],
                             f"official_platform@{datetime.now():%Y-%m-%d}")
    if psirt["status"] == "ok":
        db.save_update_cache(product, "advisories", psirt["payload"],
                             f"psirt@{datetime.now():%Y-%m-%d}")
    return {"official": {"status": official["status"], "reason": official.get("reason", "")},
            "psirt": {"status": psirt["status"], "reason": psirt.get("reason", "")}}


async def get_update_overview(sw_version: str) -> dict:
    """给定设备当前版本，汇总：最新版本、跨越版本的发布说明（四类归类）、命中的安全公告、来源标注。"""
    product = kb.product_of(sw_version)
    current = kb.normalize_version(sw_version)
    latest = kb.LATEST.get(product, "")

    sources_tried = []
    cached = db.get_update_cache(product, "release_notes")
    if cached:
        scraped_notes, src = cached["payload"], cached["source"]
        sources_tried.append({"source": src, "fetched_at": cached["fetched_at"]})
    else:
        live = await fetch_official_release_notes(product)
        sources_tried.append({"source": "official_platform", "status": live["status"],
                              "reason": live.get("reason", ""), "fetched_at": db.now()})
        scraped_notes = live.get("payload") or []

    # 发布说明：抓取到的优先，内置知识库补充跨越版本的说明
    releases = []
    for rel in kb.releases_between(product, current, latest):
        notes = scraped_notes if scraped_notes and rel.is_latest else rel.notes
        releases.append({
            "version": rel.version, "release_date": rel.release_date,
            "is_latest": rel.is_latest,
            "notes": _classify_release_notes([dict(n) for n in notes] or [dict(n) for n in rel.notes]),
            "known_issues": rel.known_issues,
            "upgrade_notes": rel.upgrade_notes,
            "data_source": "scraped" if (scraped_notes and rel.is_latest) else "builtin_snapshot",
        })

    adv_cached = db.get_update_cache(product, "advisories")
    advisory_pool = (adv_cached or {}).get("payload") or kb.PSIRT_ADVISORIES
    advisories_hit = [a for a in advisory_pool
                      if a.get("product") == product and current in (a.get("affected_versions") or [])]
    eol, eol_detail = kb.is_eol(product, current)

    return {
        "product": product,
        "product_name": kb.PRODUCT_NAMES.get(product, product),
        "current_version": current,
        "latest_version": latest,
        "up_to_date": current == latest,
        "releases": releases,
        "advisories_hit": advisories_hit,
        "eol": {"hit": eol, "detail": eol_detail},
        "sources": sources_tried,
        "fetched_at": db.now(),
    }
