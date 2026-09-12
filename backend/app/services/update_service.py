"""软件更新信息抓取服务：多源适配 + 本地缓存 + 来源标注。

来源优先级（发布说明页面免认证可抓，软件列表需登录 Cookie，按可用性自动降级）：
1. official_platform —— support.sangfor.com.cn
   - 发布说明（免认证）：AF productDocument category_id=360973、AC category_id=324129，
     内容为页面内嵌 JSON 数据，按版本段落解析【新增】/【优化】/【修复】/【安全】条目；
   - 软件下载列表（需认证）：productSoftware/list?product_id=13(AF)/22(AC)，
     仅支持 .env（SANGFOR_SUPPORT_COOKIE）配置登录 Cookie 后抓取；
2. psirt —— www.sangfor.com.cn/sec_center 安全公告（公开，详情页可直接抓取）
3. builtin —— 内置版本知识库快照（保证离线演示完整可用）

所有落库缓存均带 source 与 fetched_at；发布说明按
新增功能 / 安全修复 / 已知问题修复 / 优化 四类归类（关键词规则，确定性）。
"""
import html as htmllib
import re
from datetime import datetime

import httpx

from app import db
from app.config import settings
from app.services.knowledge import versions as kb

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SangforSupportAgent/1.0"


def _support_cookie() -> str:
    """平台 Cookie 仅支持 .env 配置（SANGFOR_SUPPORT_COOKIE），界面已不再提供输入。"""
    return settings.support_cookie

# support.sangfor.com.cn 关键栏目（调研确认的 URL 模式；发布说明免认证，软件列表需登录）
# AF 发布说明 type=1 与 AC 的 category/version 参数为用户指定的关注范围入口
OFFICIAL_URLS = {
    "af": {
        "release_notes": "https://support.sangfor.com.cn/productDocument/read?product_id=13&version_id=1197&category_id=360973&type=1",
        "software_list": "https://support.sangfor.com.cn/productSoftware/list?product_id=13",
    },
    "ac": {
        "release_notes": "https://support.sangfor.com.cn/productDocument/read?product_id=22&version_id=1196&category_id=359280",
        "software_list": "https://support.sangfor.com.cn/productSoftware/list?product_id=22",
    },
}
# 关注版本下限：低于该版本的发布说明不再展示（需求口径：AF 8.0.7 之前 / AC 13.0.62 之前不关注）
MIN_INTEREST_VERSION = {"af": "8.0.7", "ac": "13.0.62"}
CACHE_TTL_HOURS = 6            # 缓存新鲜期：期内刷新任务跳过重抓，避免重复爬取
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

_VERSION_HEAD = re.compile(r"^((?:AC&SG)|AF|AC|SG)?\s*(\d+\.\d+\.\d+)R?\d*\s*(?:版本)?")
_NOTE_MARK = re.compile(r"^【(新增|优化|修复|安全)】")
_PROSE_NOISE = {"版本概述", "新增/优化功能", "功能详细介绍", "版本说明", "升级说明",
                "新增/优化功能介绍", "序号", "功能分类"}
_PROSE_VERB = re.compile(r"支持|新增|优化|提升|增强|修复|改进|扩展|引入|提供|实现")


def _strip_html_to_lines(html_text: str) -> list[str]:
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text, flags=re.S | re.I)
    h = re.sub(r"<[^>]+>", "\n", h)
    h = htmllib.unescape(h)
    return [ln.strip() for ln in h.split("\n") if ln.strip()]


def _embedded_content_lines(raw_html: str) -> list[str]:
    """发布说明正文存于页面 <script> 内嵌 JSON（服务端渲染数据）：提取并反转义。

    保留包含【标记】或版本标题的 script 段（部分版本段为纯散文、无标记）；
    探测时先做 HTML 反转义（标题形如 AC&amp;SG13.0.121）。
    """
    content = ""
    for seg in re.findall(r"<script[^>]*>(.*?)</script>", raw_html, flags=re.S):
        probe = htmllib.unescape(seg)
        if "【新增】" in probe or "【优化】" in probe or "【修复】" in probe or "【安全】" in probe \
                or re.search(r"(?:AC&SG|AF|AC|SG)\s*\d+\.\d+\.\d+", probe):
            content += seg
    if not content:
        return _strip_html_to_lines(raw_html)   # 页面结构变化时退回全文解析
    content = content.replace("\\/", "/").replace('\\"', '"').replace("\\n", "\n")
    return _strip_html_to_lines(content)


def _prose_features(seg_lines: list[str], version: str = "") -> list[dict]:
    """无【标记】的版本段（如 8.0.107 为散文/表格混排）：拼接被标签打断的句子，
    按「；」切分出特性短句（含 支持/新增/优化/提升/增强/修复 等动词）。"""
    text = "".join(ln for ln in seg_lines
                   if ln not in _PROSE_NOISE and (len(ln) > 1 or ln in "；。：、，"))
    items = []
    seen = set()
    # 切分点：分号/句号，以及「1）」「（1）」「1.」类编号项的起始位置（编号限 1-2 位，避免误切 802.1x）
    parts = re.split(r"[；;。]|(?=[（]?\d{1,2}[）)、])", text)
    for chunk in parts:
        chunk = chunk.strip()
        # 剥掉「AC&SG13.0.121【版本主要价值】」类版本陈述前缀，保留其实际内容
        head = re.match(r"^(?:【[^】]{1,14}】)?\s*((?:AC&SG)|AF|AC|SG)?\s*\d+\.\d+\.\d+R?\d*\s*(?:版本)?[^，。；]{0,16}", chunk)
        if head:
            chunk = chunk[head.end():].lstrip("：: ，，")
        chunk = re.sub(r"^【[^】]{1,12}】", "", chunk)                  # 去掉【版本主要价值】等段标
        chunk = re.sub(r"^\d+[、.)）]\s*", "", chunk)                   # 去掉序号
        if not (10 <= len(chunk) <= 150) or not _PROSE_VERB.search(chunk):
            continue
        if re.match(r"^(AF|AC|SG)?\s*\d+\.\d+\.\d+", chunk):           # 版本陈述/升级包名
            continue
        if "详见" in chunk or "参见" in chunk or chunk in seen:
            continue
        seen.add(chunk)
        kind = "修复" if "修复" in chunk[:12] else ("新增" if re.search(r"新增|支持|引入|提供|扩展", chunk) else "优化")
        items.append({"type": kind, "title": chunk, "detail": ""})
        if len(items) >= 20:
            break
    return items


def parse_release_lines(lines: list[str]) -> dict:
    """把发布说明文本行解析为 {版本: [条目]}。

    版本段落以「AF8.0.107版本…」「AC&SG13.0.121」类陈述行为界；段内优先抓取
    【新增】/【优化】/【修复】/【安全】条目（含紧随其后的说明行），
    无标记段落（散文体）回退到特性短句提取。
    """
    heads: list[tuple[int, str]] = []
    for i, ln in enumerate(lines):
        m = _VERSION_HEAD.match(ln)
        if m and (m.group(1) or ln[m.start(2):].lstrip().startswith(f"{m.group(2)}版本")):
            heads.append((i, m.group(2)))
    merged: list[tuple[int, str]] = []
    for pos, ver in heads:
        if merged and merged[-1][1] == ver and pos - merged[-1][0] < 5:
            continue
        merged.append((pos, ver))

    result: dict[str, list[dict]] = {}
    start_range = merged if merged else [(0, "")]
    for idx, (pos, ver) in enumerate(start_range):
        end = start_range[idx + 1][0] if idx + 1 < len(start_range) else len(lines)
        seg = lines[pos:end]
        items: list[dict] = []
        for j, ln in enumerate(seg):
            m = _NOTE_MARK.match(ln)
            if not m:
                continue
            title = re.sub(r"\s+", " ", ln[m.end():]).strip().rstrip("，。；;")
            detail = ""
            if j + 1 < len(seg):
                nxt = seg[j + 1]
                if not _NOTE_MARK.match(nxt) and not _VERSION_HEAD.match(nxt) and 8 < len(nxt) <= 300:
                    detail = re.sub(r"\s+", " ", nxt).strip()
            if len(title) >= 2:
                items.append({"type": m.group(1), "title": title[:150], "detail": detail})
        if not items:
            items = _prose_features(seg, ver)
        if not items:
            continue
        bucket = result.setdefault(ver, [])
        seen = {it["title"] for it in bucket}
        bucket.extend(it for it in items if it["title"] not in seen)
    return result


async def fetch_official_release_notes(product: str) -> dict:
    """抓取官方平台『新版本发布信息』页（免认证），按版本解析功能更新条目。"""
    url = OFFICIAL_URLS.get(product, {}).get("release_notes", "")
    if not url:
        return {"source": "official_platform", "status": "unavailable", "payload": None,
                "reason": "未知产品线"}
    headers = {"User-Agent": UA}
    if _support_cookie():
        headers["Cookie"] = _support_cookie()
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, verify=False) as cli:
            resp = await cli.get(url, headers=headers)
        if resp.status_code != 200:
            return {"source": "official_platform", "status": "error", "payload": None,
                    "reason": f"HTTP {resp.status_code}"}
        lines = _embedded_content_lines(resp.text)
        parsed = parse_release_lines(lines)
        if parsed:
            payload = [{"version": v, "notes": notes} for v, notes in parsed.items()]
            return {"source": "official_platform", "status": "ok", "payload": payload,
                    "fetched_at": db.now(), "item_count": sum(len(x["notes"]) for x in payload)}
        hint = "（页面含认证提示）" if "请您先认证身份" in resp.text else ""
        return {"source": "official_platform", "status": "parse_empty", "payload": None,
                "reason": f"页面为动态渲染，未解析到发布说明条目{hint}"}
    except Exception as e:   # noqa: BLE001 —— 抓取失败一律降级，不阻塞主流程
        return {"source": "official_platform", "status": "error", "payload": None, "reason": str(e)}


async def fetch_software_list(product: str) -> dict:
    """抓取官方平台『软件下载』列表（AF: product_id=13 / AC: product_id=22）。

    列表数据为登录后 XHR/内嵌 JSON 渲染：尽力解析页面内嵌数据，
    无 Cookie 或解析不到条目时明确返回状态，前端可见降级原因。
    """
    url = OFFICIAL_URLS.get(product, {}).get("software_list", "")
    if not url:
        return {"source": "official_platform", "status": "unavailable", "payload": []}
    headers = {"User-Agent": UA}
    if _support_cookie():
        headers["Cookie"] = _support_cookie()
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as cli:
            resp = await cli.get(url, headers=headers)
        if resp.status_code != 200:
            return {"source": "official_platform", "status": "error", "payload": [],
                    "reason": f"HTTP {resp.status_code}"}
        html = resp.text
        rows = _extract_software_items(html)
        if rows:
            return {"source": "official_platform", "status": "ok", "payload": rows,
                    "fetched_at": db.now()}
        if "请您先认证身份" in html or "login" in html[:3000].lower():
            return {"source": "official_platform", "status": "auth_required", "payload": [],
                    "reason": "软件列表需登录：请在 backend/.env 配置 SANGFOR_SUPPORT_COOKIE 后重试"}
        return {"source": "official_platform", "status": "parse_empty", "payload": [],
                "reason": "列表为前端动态渲染，静态抓取未获得条目；可尝试更新 Cookie 或使用内置版本知识库"}
    except Exception as e:   # noqa: BLE001 —— 网络失败降级
        return {"source": "official_platform", "status": "error", "payload": [], "reason": str(e)}


def _extract_software_items(html: str) -> list[dict]:
    """从软件列表页提取版本条目（版本名/大小/发布时间/MD5/下载地址，尽力而为）。"""
    rows: list[dict] = []
    seen: set[str] = set()

    def _push(name: str, size: str = "", published: str = "", md5: str = "", url: str = ""):
        name = (name or "").strip()
        if not name or name in seen or not (3 < len(name) < 120):
            return
        seen.add(name)
        rows.append({"name": name, "size": size, "published": published,
                     "md5": md5, "url": (url or "").strip()})

    # 下载地址登记：条目名 → 相邻的下载链接（页面 <a href> 或内嵌 JSON url 字段）
    def _attach_url(name: str, url: str):
        if not url:
            return
        for row in rows:
            if row["name"] == name and not row.get("url"):
                row["url"] = url.strip()
                return

    # 形态1：页面内嵌 JSON 数组（vue 初始 state），含 name/size/md5 及可能的下载地址字段
    for m in re.finditer(r'\{\\"name\\":\\"([^"\\]{3,100})\\"[^}]{0,600}?\\"md5\\":\\"([a-f0-9]{16,32})\\"[^}]{0,600}?\}',
                         html, re.I):
        seg = m.group(0)
        um = re.search(r'\\\\"(?:url|downloadUrl|download_url|fileUrl)\\\\":\\\\"([^"\\\\]{5,300})\\\\"', seg)
        _push(m.group(1), md5=m.group(2), url=um.group(1) if um else "")
    for m in re.finditer(r'"name"\s*:\s*"([^"]{3,100})"\s*,\s*"size"\s*:\s*"([^"]{1,20})"', html):
        _push(m.group(1), size=m.group(2))
    # 形态2：表格/列表 DOM，形如 <td>AF_8.0.107_XXX升级包.tgz</td>…<td>2025-04-22</td>
    for m in re.finditer(r'>((?:AF|AC|SG|M6\w*|SP_[A-Z]+)[_\-][^<>\n]{3,100}?(?:\.tgz|\.zip|\.bin|\.pak|升级包|补丁包)?)<', html):
        _push(m.group(1))
    # 形态2b：下载链接 <a href="...">AF_xxx.tgz</a> / 链接文本含条目名
    for m in re.finditer(r'<a[^>]+href=["\']([^"\']+\.(?:tgz|zip|bin|pak)[^"\']*)["\'][^>]*>'
                         r'([^<]{3,100})</a>', html, re.I):
        url, label = m.group(1), m.group(2).strip()
        _push(label, url=url)
        _attach_url(label, url)
        # 链接文本与已登记条目近似匹配（文件名被截断/加前后缀时）
        base = label.rsplit("/", 1)[-1]
        for row in rows:
            if not row.get("url") and (row["name"] in base or base in row["name"]):
                row["url"] = url
    # 形态3：含版本号的附件名
    for m in re.finditer(r'([A-Za-z]{1,6}[_-]?\d+\.\d+\.\d+[^<>"\n\\]{0,60}?\.(?:tgz|zip|bin|pak))', html):
        _push(m.group(1))
    return rows[:30]


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

def _cache_fresh(record, ttl_hours: float = CACHE_TTL_HOURS) -> bool:
    """缓存是否在新鲜期内（期内不重复爬取）。"""
    if not record:
        return False
    try:
        t = datetime.fromisoformat(str(record.get("fetched_at", "")))
        return (datetime.now() - t).total_seconds() < ttl_hours * 3600
    except (ValueError, TypeError):
        return False


async def refresh_update_cache(product: str = "af", force: bool = False) -> dict:
    """手动/定时刷新：抓取官方平台（发布说明+软件列表，AF/AC 两条产品线）与 PSIRT。

    非强制模式下，缓存处于新鲜期（CACHE_TTL_HOURS 内）的条目跳过重抓，避免重复爬取。
    """
    skipped = []

    official_cache = db.get_update_cache(product, "release_notes")
    official = None
    if not force and _cache_fresh(official_cache):
        skipped.append("release_notes")
    else:
        official = await fetch_official_release_notes(product)
        if official["status"] == "ok":
            db.save_update_cache(product, "release_notes", official["payload"],
                                 f"official_platform@{datetime.now():%Y-%m-%d}")

    psirt_cache = db.get_update_cache(product, "advisories")
    psirt = None
    if not force and _cache_fresh(psirt_cache):
        skipped.append("advisories")
    else:
        psirt = await fetch_psirt_bulletins()
        if psirt["status"] == "ok":
            db.save_update_cache(product, "advisories", psirt["payload"],
                                 f"psirt@{datetime.now():%Y-%m-%d}")

    soft = {}
    for prod in ("af", "ac"):
        soft_cache = db.get_update_cache(prod, "software_list")
        if not force and _cache_fresh(soft_cache):
            skipped.append(f"software_list:{prod}")
            soft[prod] = {"status": "cached", "count": len(soft_cache["payload"] or []),
                          "reason": "缓存新鲜，跳过重抓"}
            continue
        result = await fetch_software_list(prod)
        soft[prod] = {"status": result["status"], "count": len(result.get("payload") or []),
                      "reason": result.get("reason", "")}
        if result["status"] == "ok":
            db.save_update_cache(prod, "software_list", result["payload"],
                                 f"official_platform@{datetime.now():%Y-%m-%d}")

    return {"official": {"status": (official or {}).get("status", "cached"),
                         "reason": (official or {}).get("reason", "")},
            "psirt": {"status": (psirt or {}).get("status", "cached") if psirt else "cached",
                      "reason": (psirt or {}).get("reason", "") if psirt else ""},
            "software_list": soft,
            "skipped_fresh": skipped}


async def get_software_list(product: str, force: bool = False) -> dict:
    """官方软件更新列表：缓存优先（可选强制刷新；缓存超 24h 自动重抓），AF/AC 各自一条产品线。"""
    cached = db.get_update_cache(product, "software_list")
    if cached and not force and _cache_fresh(cached, ttl_hours=24):
        return {"product": product, "status": "ok", "items": cached["payload"],
                "source": cached["source"], "fetched_at": cached["fetched_at"], "from_cache": True}
    live = await fetch_software_list(product)
    if live["status"] == "ok":
        db.save_update_cache(product, "software_list", live["payload"],
                             f"official_platform@{datetime.now():%Y-%m-%d}")
        return {"product": product, "status": "ok", "items": live["payload"],
                "source": "official_platform", "fetched_at": live.get("fetched_at", ""),
                "from_cache": False}
    if cached:   # 抓取失败时回退旧缓存
        return {"product": product, "status": "ok", "items": cached["payload"],
                "source": cached["source"] + "（历史缓存）", "fetched_at": cached["fetched_at"],
                "from_cache": True}
    return {"product": product, "status": live["status"], "items": [],
            "reason": live.get("reason", ""), "source": "official_platform",
            "fetched_at": db.now(), "from_cache": False}


async def get_update_overview(sw_version: str) -> dict:
    """给定设备当前版本，汇总：最新版本、跨越版本的发布说明（四类归类）、命中的安全公告、来源标注。

    发布说明优先使用官方平台免认证抓取的按版本真实条目，缺失版本回退内置知识库。
    """
    product = kb.product_of(sw_version)
    current = kb.normalize_version(sw_version)
    latest = kb.LATEST.get(product, "")

    sources_tried = []
    cached = db.get_update_cache(product, "release_notes")
    if cached and not _cache_fresh(cached, ttl_hours=24):
        # 缓存超过 24h：自动重抓一次（避免长期重复使用过期数据，也不阻塞响应）
        try:
            live = await fetch_official_release_notes(product)
            if live["status"] == "ok":
                db.save_update_cache(product, "release_notes", live["payload"],
                                     f"official_platform@{datetime.now():%Y-%m-%d}")
                cached = db.get_update_cache(product, "release_notes") or cached
                sources_tried.append({"source": "official_platform", "auto_refreshed": True,
                                      "fetched_at": db.now()})
        except Exception:   # noqa: BLE001 —— 刷新失败继续用旧缓存
            pass
    if cached:
        scraped_map = _scraped_version_map(cached["payload"])
        sources_tried.append({"source": cached["source"], "fetched_at": cached["fetched_at"]})
    else:
        live = await fetch_official_release_notes(product)
        sources_tried.append({"source": "official_platform", "status": live["status"],
                              "reason": live.get("reason", ""), "fetched_at": db.now()})
        if live["status"] == "ok":
            db.save_update_cache(product, "release_notes", live["payload"],
                                 f"official_platform@{datetime.now():%Y-%m-%d}")
        scraped_map = _scraped_version_map(live.get("payload"))

    # 官方功能版本集合：以用户指定入口页抓到的真实版本清单为骨架（过滤关注下限），
    # 替代内置知识库版本链——内置链可能包含官方功能页未覆盖的版本，导致路线/价值展示失真
    min_interest = MIN_INTEREST_VERSION.get(product)
    official_versions = sorted(
        [v for v in scraped_map
         if not min_interest or kb.version_key(v) >= kb.version_key(min_interest)],
        key=kb.version_key)

    releases = []
    scraped_count = 0
    if official_versions:
        # 官方优先：仅展示官方功能页覆盖的版本（≥当前版本），known_issues 从内置知识库补充
        for v in official_versions:
            if kb.version_key(v) <= kb.version_key(current):
                continue
            scraped = scraped_map.get(v) or []
            notes = _classify_release_notes([dict(n) for n in scraped])
            if not notes:
                continue
            scraped_count += 1
            builtin = kb.RELEASES.get(f"{product}:{v}")
            releases.append({
                "version": v,
                "release_date": builtin.release_date if builtin else "",
                "is_latest": v == official_versions[-1],
                "notes": notes,
                "known_issues": builtin.known_issues if builtin else [],
                "upgrade_notes": builtin.upgrade_notes if builtin else [],
                "data_source": "official_platform",
            })
    else:
        # 官方抓取缺失：回退内置知识库版本链
        for rel in kb.releases_between(product, current, latest):
            if min_interest and kb.version_key(rel.version) < kb.version_key(min_interest):
                continue
            scraped = scraped_map.get(rel.version)
            if scraped:
                notes = _classify_release_notes([dict(n) for n in scraped])
                data_source = "official_platform"
                scraped_count += 1
            else:
                notes = _classify_release_notes([dict(n) for n in rel.notes])
                data_source = "builtin_snapshot"
            releases.append({
                "version": rel.version, "release_date": rel.release_date,
                "is_latest": rel.is_latest,
                "notes": notes,
                "known_issues": rel.known_issues,
                "upgrade_notes": rel.upgrade_notes,
                "data_source": data_source,
            })

    # 最新版本以官方功能页覆盖的最高版本为准（与用户指定入口口径一致）
    if official_versions:
        latest = official_versions[-1]

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
        "scraped_note_versions": scraped_count,
        "official_versions": official_versions,
        "sources": sources_tried,
        "fetched_at": db.now(),
    }


def _scraped_version_map(payload) -> dict:
    """官方平台缓存 → {版本: [条目]}；兼容旧扁平结构（视为无效，待刷新覆盖）。"""
    if not isinstance(payload, list) or not payload:
        return {}
    if not all(isinstance(x, dict) and "version" in x and "notes" in x for x in payload):
        return {}
    return {x["version"]: x["notes"] for x in payload}
