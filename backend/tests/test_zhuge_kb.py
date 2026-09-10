"""诸葛知识库服务测试：mock 技能客户端（WS 缓冲协议），验证引用链接提取、澄清反问与降级路径。"""
import asyncio
import json

import pytest

from app.services import zhuge_kb_service


class _FakeClient:
    """模拟技能客户端的 WS 缓冲协议（send_message + _response_buffer + _clean_html）。"""

    def __init__(self, mode):
        self.product = None
        self.mode = mode
        self._response_buffer = []

    def send_message(self, q, product=None):
        self._response_buffer = []
        if self.mode == "ok":
            self._response_buffer = [
                {"eventType": "message", "a": "官方"},
                {"eventType": "message", "a": "答案，详见文末"},
                {"eventType": "dict", "a": json.dumps([
                    {"title": "官方文档A", "content": '说明 <a href="https://kb.sangfor.com.cn/k1">详情</a>'},
                    {"title": "官方文档B", "content": "纯文本"},
                    "bad-ref"], ensure_ascii=False)},
                {"eventType": "finalMessage", "a": ""},
            ]
        elif self.mode == "clarify":
            self._response_buffer = [
                {"eventType": "reasoning", "a": "思考"},
                {"eventType": "ask_user_question", "a": json.dumps(
                    {"questions": [{"prompt": "请确认您的 AF 版本范围",
                                    "options": [{"label": "AF8.0.35 - AF8.0.107"},
                                                {"label": "AF7.4 - AF8.0.32"}]}]},
                    ensure_ascii=False)},
            ]
        return True

    def _clean_html(self, s):
        return s


class _FakeAgent:
    def __init__(self, ok=True, mode="ok", **_kw):
        self.ok, self.mode = ok, mode
        self.client = _FakeClient(mode)
        self.closed = False

    def login_by_sso(self, bbs_username=None, bbs_password=None):
        return self.ok

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_module(monkeypatch, request):
    mode = getattr(request, "param", "ok")
    agent = {"mode": mode}

    class _Mod:
        CONFIG = {"bbs_username": "u", "bbs_password": "p"}
        ZhugeAISubAgent = staticmethod(lambda **kw: _FakeAgent(mode=agent["mode"], **kw))

    monkeypatch.setattr(zhuge_kb_service, "_load_skill_module", lambda: _Mod)
    monkeypatch.setattr(zhuge_kb_service._ZhugeSession, "CLARIFY_GRACE", 0.5)
    zhuge_kb_service.reset_sessions()
    yield _Mod
    zhuge_kb_service.reset_sessions()


def test_ask_ok_and_references_sanitized(fake_module):
    r = asyncio.run(zhuge_kb_service.ask_official_kb("AC怎么配置绑定？", product="AC"))
    assert r["status"] == "ok"
    assert r["answer"] == "官方答案，详见文末"
    assert r["product"] == "AC"
    # 引用 + 从 HTML 还原的超链接（顺序：A → A内容里的链接 → B → 纯串）
    titles = [x["title"] for x in r["references"]]
    assert titles == ["官方文档A", "详情", "官方文档B", "bad-ref"]
    by_url = {x["url"]: x for x in r["references"] if x.get("url")}
    assert by_url["https://kb.sangfor.com.cn/k1"]["title"] == "详情"
    assert all(len(x.get("content", "")) <= 200 for x in r["references"])


def test_ask_clarification_fast_return(fake_module, monkeypatch):
    """官方反问（ask_user_question）：快速返回澄清问题而非等到超时。"""
    import time
    monkeypatch.setattr(fake_module, "ZhugeAISubAgent",
                        staticmethod(lambda **kw: _FakeAgent(mode="clarify", **kw)))
    zhuge_kb_service.reset_sessions()
    start = time.time()
    r = asyncio.run(zhuge_kb_service.ask_official_kb("AF防火墙WEB API怎么开启？"))
    assert r["status"] == "ok"
    assert r.get("clarification") is True
    assert "请确认您的 AF 版本范围" in r["answer"]
    assert "AF8.0.35 - AF8.0.107" in r["answer"]
    assert time.time() - start < 10   # 未等到 60s 超时


def test_ask_empty_question_degrades(fake_module):
    r = asyncio.run(zhuge_kb_service.ask_official_kb("  "))
    assert r["status"] == "error"


def test_ask_login_failure_degrades(fake_module, monkeypatch):
    monkeypatch.setattr(fake_module, "ZhugeAISubAgent",
                        staticmethod(lambda **kw: _FakeAgent(ok=False, **kw)))
    zhuge_kb_service.reset_sessions()
    r = asyncio.run(zhuge_kb_service.ask_official_kb("测试问题"))
    assert r["status"] == "error"
    assert "SSO" in r["reason"]


def test_product_mapping():
    assert zhuge_kb_service.product_from_device_type("af") == "AF"
    assert zhuge_kb_service.product_from_device_type("ac") == "AC"
    assert zhuge_kb_service.product_from_device_type("") == "AF"


def test_extract_links_strips_tags_and_validates_scheme():
    html = '<a href="https://a.b/c">链接一</a> 文本 <a href="javascript:void(0)">坏链接</a>'
    links = zhuge_kb_service._extract_links(html)
    assert links == [{"title": "链接一", "url": "https://a.b/c"}]


def test_norm_ref_keeps_url_fields():
    ref = zhuge_kb_service._norm_ref({"title": "T", "doc_url": "https://d.e/f", "content": "c"})
    assert ref["url"] == "https://d.e/f"
    assert zhuge_kb_service._norm_ref("纯串标题") == {"title": "纯串标题"}
    assert zhuge_kb_service._norm_ref({"title": ""}) is None


def test_tool_handler_shape(fake_module):
    from app.agent import tools
    device = {"id": "d", "name": "测试", "type": "ac", "mode": "simulator"}
    tool = tools.TOOLS_BY_NAME["search_official_knowledge"]
    assert not tool.write
    result = asyncio.run(tool.handler(None, {"question": "测试问题"}, device))
    assert result["status"] == "ok"
    assert "官方参考" in result["_llm_summary"]


def test_tool_handler_clarify_guidance(fake_module, monkeypatch):
    monkeypatch.setattr(fake_module, "ZhugeAISubAgent",
                        staticmethod(lambda **kw: _FakeAgent(mode="clarify", **kw)))
    zhuge_kb_service.reset_sessions()
    from app.agent import tools
    device = {"id": "d", "name": "测试", "type": "af", "mode": "simulator"}
    tool = tools.TOOLS_BY_NAME["search_official_knowledge"]
    result = asyncio.run(tool.handler(None, {"question": "测试问题"}, device))
    assert result.get("clarification") is True
    assert "澄清问题" in result["_llm_summary"]


def test_tool_handler_degrade_message(fake_module, monkeypatch):
    monkeypatch.setattr(fake_module, "ZhugeAISubAgent",
                        staticmethod(lambda **kw: _FakeAgent(ok=False, **kw)))
    zhuge_kb_service.reset_sessions()
    from app.agent import tools
    device = {"id": "d", "name": "测试", "type": "af", "mode": "simulator"}
    tool = tools.TOOLS_BY_NAME["search_official_knowledge"]
    result = asyncio.run(tool.handler(None, {"question": "测试问题"}, device))
    assert result["status"] == "error"
    assert "不要编造" in result["_llm_summary"]
