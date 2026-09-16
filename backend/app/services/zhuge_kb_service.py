"""诸葛小T官方知识库服务：包装 skills/zhuge-ai-assistant 技能客户端。

- 直接按路径加载技能脚本复用（不复制代码），技能脚本依赖 requests/pycryptodome/websocket-client；
- 凭据链：『平台设置』界面(DB) → .env → 技能 CONFIG 内置账号兜底（BBSLogin 原生支持）；
- 客户端为同步实现（requests + websocket-client），统一经 asyncio.to_thread 进线程池调用；
- 每条产品线（AF/AC）各持一条登录会话并缓存复用（SSO JWT 约 7 天有效）；
- 引用来源：官方回答 HTML 中的 <a> 超链接会被技能的 _clean_html 剥掉，这里从
  answer_raw 中提取还原为 {title, url}，保证「官方参考」可点击；
- 对外统一降级返回 {status, answer, references, reason}，任何失败不抛异常、不阻塞对话主流程。
"""
import asyncio
import importlib.util
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

from app.config import PROJECT_DIR, settings
from app.services import app_settings

log = logging.getLogger("sangfor-agent")

SKILL_SCRIPT = PROJECT_DIR / "skills" / "zhuge-ai-assistant" / "scripts" / "zhuge_ai_client.py"
KB_SOURCE = "zhuge_official_kb"
# 外层总超时（默认 45s，KB_ASK_TIMEOUT 可调）：超时后编排 LLM 基于本地知识库/设备数据降级作答；
# 内层轮询先行超时（见 _ZhugeSession.ask），给外层留出降级处理时间
ASK_TIMEOUT = settings.kb_ask_timeout
MAX_REFS = 8

# 官方回答 HTML 中的超链接（引用文档/下载地址），以及引用条目可能携带的 URL 字段
_LINK_RE = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
URL_KEYS = ("url", "link", "href", "file_url", "doc_url", "jump_url", "web_url")

# 技能模块（进程内缓存）与会话池（产品线 → 会话）
_mod = None
_mod_lock = threading.Lock()
_sessions: dict[str, "_ZhugeSession"] = {}
_sessions_lock = threading.Lock()


def _load_skill_module():
    """按路径加载技能脚本；两个 requests.Session 统一 trust_env=False（技能要求绕过系统代理）。"""
    global _mod
    if _mod is not None:
        return _mod
    with _mod_lock:
        if _mod is not None:
            return _mod
        if not SKILL_SCRIPT.exists():
            raise FileNotFoundError(f"zhuge 技能脚本不存在：{SKILL_SCRIPT}")
        spec = importlib.util.spec_from_file_location("zhuge_ai_client", SKILL_SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)

        _orig_bbs_init = mod.BBSLogin.__init__

        def _bbs_init(self, *a, **k):
            _orig_bbs_init(self, *a, **k)
            self.session.trust_env = False

        mod.BBSLogin.__init__ = _bbs_init
        _orig_client_init = mod.ZhugeAIClient.__init__

        def _client_init(self, *a, **k):
            _orig_client_init(self, *a, **k)
            self.session.trust_env = False

        mod.ZhugeAIClient.__init__ = _client_init
        _mod = mod
        return mod


def has_builtin_credentials() -> bool:
    """技能 CONFIG 是否带内置社区账号（供设置页状态展示）。依赖缺失时如实返回 False。"""
    try:
        cfg = _load_skill_module().CONFIG
        return bool(cfg.get("bbs_username")) and bool(cfg.get("bbs_password"))
    except Exception:   # noqa: BLE001
        return False


def _credentials() -> tuple[str, str]:
    user, pwd, _source = app_settings.get_zhuge_credentials()
    if user and pwd:
        return user, pwd
    try:   # 技能内置账号兜底（BBSLogin 亦原生支持 None 回退）
        cfg = _load_skill_module().CONFIG
        return cfg.get("bbs_username", ""), cfg.get("bbs_password", "")
    except Exception:   # noqa: BLE001
        return "", ""


def product_from_device_type(device_type: str) -> str:
    dt = str(device_type).lower()
    if dt == "ac":
        return "AC"
    if dt == "scp":
        return "SCP"
    return "AF"


def _extract_links(*html_sources) -> list[dict]:
    """从官方回答的 HTML 中提取 <a> 超链接（_clean_html 会剥掉它们，这里找回）。"""
    links, seen = [], set()
    for html in html_sources:
        if not html:
            continue
        for m in _LINK_RE.finditer(html):
            url = (m.group(1) or "").strip()
            title = _TAG_RE.sub("", m.group(2) or "").strip() or url
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            links.append({"title": title[:120], "url": url})
    return links


def _norm_ref(r) -> dict | None:
    """规整单条引用：保留标题/摘要，并保留引用条目自带的 URL 字段（若有）。"""
    if isinstance(r, str):
        return {"title": r.strip()[:150]} if r.strip() else None
    if not isinstance(r, dict):
        return None
    out: dict = {"title": str(r.get("title") or "").strip()[:150]}
    for k in URL_KEYS:
        v = r.get(k)
        if isinstance(v, str) and v.startswith(("http://", "https://")):
            out["url"] = v.strip()
            break
    content = r.get("content")
    if isinstance(content, str) and content.strip():
        out["content"] = content.strip()[:200]
    return out if out["title"] or out.get("url") else None


class _ZhugeSession:
    """一条产品线一个会话：SSO 登录 + WS 连接 + 问答（全部同步，供线程池调用）。

    问答走自建的缓冲轮询（复用技能脚本的 send_message/_clean_html，不改脚本）：
    官方后端已升级为会反问的智能体流程（ask_user_question 事件要求用户澄清
    版本范围等），技能的 ask_full 不处理该事件会白等到超时，这里检测到反问
    后快速返回，交由编排 LLM 结合设备信息作答或换具体问法重查。
    """
    CLARIFY_GRACE = settings.kb_clarify_grace     # 反问出现后再等几秒，确认没有正式回答尾随

    def __init__(self, product: str):
        self.product = product
        self.lock = threading.Lock()
        self.agent = None

    def _ensure_ready(self):
        if self.agent is not None:
            return
        mod = _load_skill_module()
        user, pwd = _credentials()
        agent = mod.ZhugeAISubAgent(mobile=user or None, password=pwd or None)
        # login_by_sso 内部：社区登录 → ticket → JWT → connect_ws
        if not agent.login_by_sso():
            raise RuntimeError("社区SSO登录失败：请检查『平台设置』中的社区账号密码（或技能内置账号有效性）")
        agent.client.product = self.product
        self.agent = agent
        log.info("诸葛知识库会话已建立 product=%s", self.product)

    def _reset(self):
        try:
            if self.agent is not None:
                self.agent.close()
        except Exception:   # noqa: BLE001
            pass
        self.agent = None

    def _clarification_text(self) -> str | None:
        """从 WS 缓冲提取反问（澄清）问题，格式化为可读文本。"""
        text = None
        for ev in getattr(self.agent.client, "_response_buffer", []) or []:
            if ev.get("eventType") != "ask_user_question":
                continue
            try:
                d = json.loads(ev.get("a") or "{}")
            except (ValueError, TypeError):
                continue
            lines = []
            for q in d.get("questions") or []:
                prompt = str(q.get("prompt") or "").strip()
                opts = [str(o.get("label")) for o in q.get("options") or [] if o.get("label")]
                if prompt:
                    lines.append(prompt + (f"（可选：{'；'.join(opts[:6])}）" if opts else ""))
            if lines:
                text = "；".join(lines)
        return text

    def _ask_via_buffer(self, question: str, timeout: float = 60.0) -> dict:
        """发送问题并轮询 WS 缓冲：正式回答优先；检测到反问且无尾随回答时快速返回。"""
        client = self.agent.client
        client._response_buffer = []
        if not client.send_message(question, product=self.product):
            raise RuntimeError("WebSocket 未连接")
        deadline, clarify_at, refs = time.time() + timeout, None, []
        while time.time() < deadline:
            answer_parts, final = [], False
            for ev in client._response_buffer:
                et = ev.get("eventType")
                if et == "message":
                    answer_parts.append(ev.get("a") or "")
                elif et == "dict":
                    try:
                        parsed = json.loads(ev.get("a") or "[]")
                        if isinstance(parsed, list):
                            refs = parsed
                    except (ValueError, TypeError):
                        pass
                elif et == "finalMessage":
                    final = True
                elif et == "ask_user_question" and clarify_at is None:
                    clarify_at = time.time()
            if final:
                return {"answer": client._clean_html("".join(answer_parts)).strip(),
                        "references": refs, "complete": True}
            if clarify_at is not None and time.time() - clarify_at > self.CLARIFY_GRACE:
                clar = self._clarification_text()
                if clar:
                    return {"answer": clar, "references": refs,
                            "clarification": True, "complete": True}
            time.sleep(max(0.05, settings.kb_poll_interval))
        return {"answer": "", "references": refs, "complete": False}

    def ask(self, question: str) -> dict:
        # 内层先行超时：赶在外层 wait_for 之前返回，给上层留出降级处理时间
        inner_timeout = max(10.0, settings.kb_ask_timeout - 10.0)
        with self.lock:
            self._ensure_ready()   # 登录失败直接抛（不重试，避免账号错误时双倍等待）
            try:
                result = self._ask_via_buffer(question, timeout=inner_timeout)
            except Exception:   # noqa: BLE001 —— 连接可能失效：重建会话后重试一次
                self._reset()
                self._ensure_ready()
                result = self._ask_via_buffer(question, timeout=inner_timeout)
            if not (result or {}).get("answer"):
                raise RuntimeError("诸葛知识库连接未就绪")
            return result


def _get_session(product: str) -> _ZhugeSession:
    with _sessions_lock:
        if product not in _sessions:
            _sessions[product] = _ZhugeSession(product)
        return _sessions[product]


def reset_sessions() -> None:
    """清空会话池（测试或凭据变更后调用）。"""
    with _sessions_lock:
        for s in _sessions.values():
            s._reset()
        _sessions.clear()


_prewarm_task: asyncio.Task | None = None


def prewarm() -> None:
    """后台预热官方知识库会话：凭据可用时提前完成 SSO 登录与 WS 建连，
    首次知识库提问不再支付登录等待（数秒~15s）。失败静默，不影响启动与使用。
    """
    global _prewarm_task

    async def _run() -> None:
        try:
            user, pwd = _credentials()
            if not (user and pwd):
                return
            for product in ("AF", "AC"):
                session = _get_session(product)
                await asyncio.to_thread(session._ensure_ready)
            log.info("诸葛知识库会话预热完成（AF/AC）")
        except Exception:   # noqa: BLE001 —— 预热失败仅记录，首次提问时按原有链路登录
            log.info("诸葛知识库会话预热未完成（首次提问时将按需登录）")

    try:
        _prewarm_task = asyncio.get_running_loop().create_task(_run())
    except RuntimeError:   # 无事件循环（如导入期）时跳过
        pass


async def ask_official_kb(question: str, product: str = "AF", timeout: float = ASK_TIMEOUT) -> dict:
    """查询深信服官方知识库。统一降级返回，不抛异常。"""
    base = {"status": "ok", "answer": "", "references": [],
            "source": KB_SOURCE, "product": product}
    if not (question or "").strip():
        return {**base, "status": "error", "reason": "问题不能为空"}
    try:
        _load_skill_module()
    except Exception as e:   # noqa: BLE001 —— 技能或依赖不可用
        return {**base, "status": "unavailable",
                "reason": f"知识库技能不可用（缺依赖或文件缺失）：{e}"}
    session = _get_session(product)
    try:
        data = await asyncio.wait_for(asyncio.to_thread(session.ask, question.strip()), timeout=timeout)
    except asyncio.TimeoutError:
        return {**base, "status": "error", "reason": f"知识库响应超时（>{int(timeout)}s），请稍后重试"}
    except Exception as e:   # noqa: BLE001
        return {**base, "status": "error", "reason": f"知识库查询失败：{e}"}
    answer = (data or {}).get("answer", "")
    if not answer:
        return {**base, "status": "error", "reason": "知识库未返回内容，请换个问法或稍后重试"}
    # 引用规整 + 从引用内容与回答 HTML 中还原被剥离的超链接（去重、限量）
    refs: list[dict] = []
    seen_urls: set[str] = set()
    for raw_ref in (data or {}).get("references") or []:
        if len(refs) >= MAX_REFS:
            break
        ref = _norm_ref(raw_ref)
        if ref is None:
            continue
        if ref.get("url"):
            seen_urls.add(ref["url"])
        refs.append(ref)
        for link in _extract_links(raw_ref.get("content") if isinstance(raw_ref, dict) else None):
            if link["url"] not in seen_urls:
                seen_urls.add(link["url"])
                refs.append(link)
    for link in _extract_links((data or {}).get("answer_raw")):
        if len(refs) >= MAX_REFS:
            break
        if link["url"] not in seen_urls:
            seen_urls.add(link["url"])
            refs.append(link)
    result = {**base, "answer": answer, "references": refs[:MAX_REFS]}
    if data.get("clarification"):
        result["clarification"] = True
    return result
