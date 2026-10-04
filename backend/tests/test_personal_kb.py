"""个人知识库测试：词条 CRUD/去重/待沉淀定位/统计 + WIKI 解析 + 无 LLM 降级。"""
import asyncio
import json

import pytest

from app import db
from app.services import personal_kb_service as pks


def _entry(topic, tags=None, conv="conv_x"):
    return {"conv_id": conv, "topic": topic, "category": "配置方法",
            "summary": "摘要", "content_md": "正文", "key_points": ["k1"],
            "tags": tags or [], "references": ["官方文档A"]}


def test_entry_crud_and_topic_dedup():
    rec = db.save_kb_entry(_entry("AC 绑定配置"))
    assert rec["id"].startswith("kb_")
    assert rec["tags"] == [] and rec["references"] == ["官方文档A"]
    assert db.kb_entry_topic_exists("AC 绑定配置")
    assert not db.kb_entry_topic_exists("不存在主题")
    db.save_kb_entry(_entry("NAT 配置", tags=["NAT", "AF"]))
    assert db.list_kb_entries(category="配置方法")
    assert db.list_kb_entries(keyword="NAT")
    db.delete_kb_entry(rec["id"])
    assert db.get_kb_entry(rec["id"]) is None


def test_pending_convs_requires_kb_hit():
    """待沉淀只收官方知识库实际命中（agent.kb.hit）/明确沉淀；调用未命中不入队。"""
    assert db.list_pending_kb_convs() == []
    conv_id = db.create_conversation("测试对话")["id"]
    # 仅调用过知识库（agent.tool.search_official_knowledge 审计）但未命中：不入队
    db.audit("agent.tool.search_official_knowledge", {"args": {}}, conv_id=conv_id)
    assert conv_id not in db.list_pending_kb_convs()
    # 实际命中（agent.kb.hit）：入队
    db.audit("agent.kb.hit", {"args": {"question": "AF怎么开API"}}, conv_id=conv_id)
    assert conv_id in db.list_pending_kb_convs()
    # 沉淀后不再出现在待沉淀列表
    db.save_kb_entry(_entry("某主题", conv=conv_id))
    assert conv_id not in db.list_pending_kb_convs()


def test_kb_turn_messages_keeps_only_kb_turns():
    """沉淀材料只保留知识库相关轮次：设备查询/寒暄轮次被过滤。"""
    kb_tool = {"role": "tool", "content": {"tool_call_id": "c2", "name": "search_official_knowledge",
                                           "content": '{"source": "zhuge_official_kb", "answer": "步骤", "references": []}'}}
    dev_tool = {"role": "tool", "content": {"tool_call_id": "c1", "name": "get_status",
                                            "content": '{"source": "device", "cpu": 12}'}}
    messages = [
        {"role": "user", "content": {"text": "看下CPU"}},                     # 普通轮次 → 过滤
        {"role": "assistant", "content": {"text": "", "tool_calls": [{"id": "c1"}]}},
        dev_tool,
        {"role": "assistant", "content": {"text": "CPU 12%，正常", "tool_calls": []}},
        {"role": "user", "content": {"text": "AF怎么开WEB API？"}},           # 知识库轮次 → 保留
        {"role": "assistant", "content": {"text": "", "tool_calls": [{"id": "c2"}]}},
        kb_tool,
        {"role": "assistant", "content": {"text": "开启步骤如下…", "tool_calls": []}},
    ]
    kept = pks._kb_turn_messages(messages)
    texts = [m["content"].get("text", "") for m in kept if m["role"] in ("user", "assistant")]
    assert "AF怎么开WEB API？" in texts and "开启步骤如下…" in texts
    assert "看下CPU" not in texts and "CPU 12%，正常" not in texts
    material = pks._conversation_text(kept)
    assert "知识库回答" in material and "知识库引用" in material   # 官方返回内容进入材料
    assert '"source"' not in material and "cpu" not in material    # 设备工具结果不进入材料
    # 引用收集同样只来自筛选后的材料（无 url 引用 → 空）
    assert pks._official_refs(kept) == []


def test_conv_has_audit_distinguishes_explicit_record():
    conv_id = db.create_conversation("显式沉淀判断")["id"]
    assert not db.conv_has_audit(conv_id, pks.KB_RECORD_ACTION)
    db.audit(pks.KB_RECORD_ACTION, {"args": {"note": "沉淀一下"}}, conv_id=conv_id)
    assert db.conv_has_audit(conv_id, pks.KB_RECORD_ACTION)
    assert not db.conv_has_audit(conv_id, pks.KB_HIT_ACTION)


def test_parse_entries_variants():
    assert pks._parse_entries('[]') == []
    assert pks._parse_entries('没有沉淀价值') == []
    assert pks._parse_entries('not json [ at all') == []
    arr = '[{"topic": "T", "category": "配置方法", "summary": "s", "content_md": "c", "key_points": [], "tags": [], "references": []}]'
    assert pks._parse_entries(arr)[0]["topic"] == "T"
    assert pks._parse_entries(f"```json\n{arr}\n```")[0]["topic"] == "T"
    assert pks._parse_entries("前缀文字\n" + arr + "\n后缀")[0]["topic"] == "T"
    # 缺 topic 的元素被过滤
    assert pks._parse_entries('[{"summary": "无主题"}]') == []


def test_parse_entries_truncated_repair():
    """max_tokens 截断：残尾被丢弃，已完整生成的词条保留。"""
    ok_entry = '{"topic": "A", "category": "配置方法", "summary": "s", "content_md": "m", "key_points": [], "tags": [], "references": []}'
    truncated = ('[\n  ' + ok_entry + ',\n  {"topic": "B", "summary": "正文被切断在半'
                 '{"x": [1, 2], "note": "引号\\"内部}的花括号不算边界", "key_points": ["未完')
    out = pks._parse_entries(truncated)
    assert [e["topic"] for e in out] == ["A"]
    # 截断发生在第一个对象内部时，返回空但不抛异常
    assert pks._parse_entries('[{"topic": "A", "summary": "切断') == []
    # 字符串内部的 ] } 不应误判为边界
    tricky = '[{"topic": "T", "content_md": "文本含 ] 和 } 与 \\" 转义", "summary": "s"}]'
    assert pks._parse_entries(tricky)[0]["topic"] == "T"


def test_generate_skipped_without_llm():
    # conftest 清空 LLM Key → 走降级路径
    r = asyncio.run(pks.generate_entries_for_conv("conv_missing"))
    assert r["status"] == "skipped"
    r2 = asyncio.run(pks.generate_reflection())
    assert r2["status"] == "skipped"


def test_norm_entry_refs():
    refs = pks._norm_entry_refs([
        {"title": "T", "url": "https://a.b/c"},
        {"title": "纯标题"},
        "https://x.y/z",
        "纯字符串引用",
        {"url": "ftp://bad"},          # 非 http(s) 且无标题 → 丢弃
        {"foo": "bar"},                # 无标题无链接 → 丢弃
    ])
    assert refs[0] == {"title": "T", "url": "https://a.b/c"}
    assert refs[1] == {"title": "纯标题"}
    assert refs[2]["url"] == "https://x.y/z"
    assert refs[3] == {"title": "纯字符串引用"}
    assert len(refs) == 4


def test_merge_refs_appends_official_links():
    llm = pks._norm_entry_refs([{"title": "T1"}])
    official = pks._norm_entry_refs([
        {"title": "官方文档", "url": "https://support.sangfor.com.cn/doc1"},
        {"title": "T1", "url": "https://dup/x"},   # url 去重看链接本身
    ])
    merged = pks._merge_refs(llm, official)
    assert merged[0] == {"title": "T1"}
    assert {"title": "官方文档", "url": "https://support.sangfor.com.cn/doc1"} in merged
    # 去重按 url：两个不同链接都保留
    assert len(merged) == 3


def test_normalize_tags_dedups_and_strips_brand():
    tags = db._normalize_tags(["深信服AF", "af ", "WEB  API", "WEB API", "SANGFOR-AC", "", "AF防火墙"])
    assert tags == ["AF", "WEB API", "AC", "AF防火墙"]   # 品牌前缀去除、组内大小写/空格去重


def test_group_tags_merges_synonym_variants():
    entries = [
        {"tags": ["深信服AF", "WEB API"]},
        {"tags": ["AF"]},
        {"tags": ["AF", "API"]},
        {"tags": ["web api"]},                   # 与 WEB API 归一化后互含 → 同组
        {"tags": ["API安全"]},                   # 共享拉丁词干 api → 并入 WEB API 组
        {"tags": ["AF防火墙"]},                  # 共享拉丁词干 af → 并入 AF 组
    ]
    grouped = db.group_tags(entries)
    names = {g["name"]: g["value"] for g in grouped}
    # AF 家族（深信服AF×1 + AF×2 + AF防火墙×1 = 4）与 API 家族（WEB API/API/web api/API安全 = 4）各归一组
    assert names.get("AF") == 4
    assert names.get("WEB API") == 4
    assert not any(g in names for g in ("深信服AF", "AF防火墙", "API安全"))
    dmap = db.tag_display_map(entries)
    assert dmap["深信服AF"] == "AF" and dmap["web api"] == "WEB API"
    assert dmap["AF防火墙"] == "AF" and dmap["API安全"] == "WEB API"


def test_filter_entries_by_range():
    entries = [{"created_at": "2026-09-01T10:00:00"}, {"created_at": "2026-09-05T10:00:00"},
               {"created_at": "2026-09-10T10:00:00"}]
    assert len(pks._filter_entries_by_range(entries, "2026-09-02", "2026-09-09")) == 1
    assert len(pks._filter_entries_by_range(entries, "", "")) == 3
    assert len(pks._filter_entries_by_range(entries, "2026-09-05", "")) == 2


def test_pending_dismiss_and_items():
    """待沉淀明细可查看提问；忽略后移出队列且沉淀跳过。"""
    conv_id = db.create_conversation("沉淀管理测试")["id"]
    db.audit("agent.kb.hit",
             {"args": {"question": "AF怎么开API"}}, conv_id=conv_id)
    item = next((i for i in pks.pending_items() if i["conv_id"] == conv_id), None)
    assert item is not None
    assert item["kb_questions"] == ["AF怎么开API"]
    assert item["msg_count"] >= 0 and item["title"] == "沉淀管理测试"
    # 忽略：队列移除 + 沉淀跳过
    db.dismiss_kb_conv(conv_id)
    assert db.kb_conv_dismissed(conv_id)
    assert conv_id not in db.list_pending_kb_convs()
    r = asyncio.run(pks.generate_entries_for_conv(conv_id, force=True))
    assert r["status"] == "skipped" and "移出" in r["reason"]


def test_process_pending_selected_convs():
    """process_pending 支持 convs 指定目标。"""
    c1 = db.create_conversation("选择沉淀A")["id"]
    c2 = db.create_conversation("选择沉淀B")["id"]
    for c in (c1, c2):
        db.audit("agent.kb.hit", {"args": {}}, conv_id=c)
    r = asyncio.run(pks.process_pending(convs=[c1]))
    assert r["processed"] == 1 and r["results"][0]["conv_id"] == c1


def test_topic_update_keeps_entry_fresh():
    """同主题沉淀：刷新词条内容而非跳过，保留原 id 与创建时间。"""
    e1 = db.save_kb_entry(_entry("AC绑定配置主题", conv="conv_old"))
    got = db.get_kb_entry_by_topic("AC绑定配置主题")
    assert got["id"] == e1["id"]
    db.update_kb_entry_content(e1["id"], "conv_new", "新摘要", "新正文",
                               ["k1"], [{"title": "官方", "url": "https://a.b/c"}], ["tag"])
    got2 = db.get_kb_entry_by_topic("AC绑定配置主题")
    assert got2["id"] == e1["id"]                      # 原 id 保留（图谱节点不悬空）
    assert got2["summary"] == "新摘要"
    # 合并式更新：旧引用保留 + 新引用并入
    urls = [r.get("url", "") if isinstance(r, dict) else "" for r in got2["references"]]
    titles = [r.get("title", "") if isinstance(r, dict) else str(r) for r in got2["references"]]
    assert "https://a.b/c" in urls      # 新引用并入
    assert any("官方文档A" in t for t in titles)   # 旧引用保留（合并不覆盖）
    assert got2["created_at"] == e1["created_at"]      # 创建时间不变


def test_get_messages_since_id_incremental():
    conv = db.create_conversation("增量测试")
    db.add_message(conv["id"], "user", {"text": "旧消息1"})
    db.add_message(conv["id"], "assistant", {"text": "旧回复"})
    since = db.max_message_id(conv["id"])
    db.add_message(conv["id"], "user", {"text": "新消息"})
    msgs = db.get_messages(conv["id"], since_id=since)
    assert [m["content"]["text"] for m in msgs] == ["新消息"]
    assert db.max_message_id(conv["id"]) > since


def test_persist_entries_create_update_and_extra_refs():
    """_persist_entries：新增/同主题更新；extra_refs（来源链接）合并进引用。"""
    entries = [{"topic": "链接沉淀主题", "category": "最佳实践", "summary": "s", "content_md": "c",
                "key_points": ["k"], "tags": ["t"], "references": []}]
    saved, updated = pks._persist_entries(entries, conv_id="",
                                          extra_refs=[{"title": "来源：X", "url": "https://a.b/c"}])
    assert (saved, updated) == (1, 0)
    got = db.get_kb_entry_by_topic("链接沉淀主题")
    assert got["references"][0]["url"] == "https://a.b/c"
    # 同主题再沉淀：更新而非新增
    saved2, updated2 = pks._persist_entries(
        [{**entries[0], "summary": "新摘要"}], conv_id="",
        extra_refs=[{"title": "来源：X", "url": "https://a.b/c"}])
    assert (saved2, updated2) == (0, 1)
    assert db.get_kb_entry_by_topic("链接沉淀主题")["summary"] == "新摘要"


def test_ingest_url_validation_and_unreachable():
    import asyncio
    # 非法 URL：直接校验失败，不发请求
    r = asyncio.run(pks.ingest_url("ftp://bad"))
    assert r["status"] == "error" and "http(s)" in r["reason"]
    # 不可达地址：抓取失败优雅降级
    r2 = asyncio.run(pks.ingest_url("http://127.0.0.1:9/page"))
    assert r2["status"] == "error" and "抓取失败" in r2["reason"]


def test_ingest_tool_registered_and_available():
    from app.agent import skills
    from app.agent.tools import TOOLS_BY_NAME
    tool = TOOLS_BY_NAME["ingest_url_to_kb"]
    assert tool.write and tool.prepare is not None
    # 任意技能下均可用（用户对话要求沉淀时的入口）
    names = {t.name for t in skills.resolve_skill_tools(skills.select_skill("看设备状态", "af"), "af")}
    assert "ingest_url_to_kb" in names and "record_to_kb" in names


def test_stats_and_reflections():
    db.save_kb_entry(_entry("统计词条A", tags=["t1", "t2"]))
    db.save_kb_entry(_entry("统计词条B", tags=["t1"]))
    stats = db.kb_stats()
    assert stats["total"] >= 2
    assert any(c["name"] == "配置方法" for c in stats["categories"])
    assert stats["tags"][0]["name"] == "t1"
    assert stats["timeline"]
    rec = db.save_kb_reflection("# 报告", {"total": stats["total"]}, period="测试期")
    assert db.list_kb_reflections()[0]["id"] == rec["id"]


# ---------- 沉淀进度 ----------

@pytest.mark.asyncio
async def test_sediment_status_progress_done(device_id, monkeypatch):
    """自动沉淀在待沉淀队列留下进度：完成时记录新增/更新条数。"""
    conv_id = db.create_conversation("进度完成测试")["id"]
    done = asyncio.Event()

    async def fake_generate(c, force=False, since_id=None):
        done.set()
        return {"status": "ok", "conv_id": c, "generated": 2, "saved": 2, "updated": 1}

    monkeypatch.setattr(pks, "generate_entries_for_conv", fake_generate)
    pks.schedule_sediment(conv_id)
    await asyncio.wait_for(done.wait(), timeout=2)
    await asyncio.sleep(0.05)   # 等状态落库
    st = db.get_kb_sediment_status(conv_id)
    assert st["status"] == "done" and st["saved"] == 2 and "更新 1" in st["note"]


@pytest.mark.asyncio
async def test_sediment_status_progress_skipped(device_id, monkeypatch):
    """跳过/失败路径同样可见原因，便于在待沉淀列表排查。"""
    conv_id = db.create_conversation("进度跳过测试")["id"]

    async def fake_skip(c, force=False, since_id=None):
        return {"status": "skipped", "conv_id": c, "reason": "对话内容过少，无沉淀价值"}

    monkeypatch.setattr(pks, "generate_entries_for_conv", fake_skip)
    pks.schedule_sediment(conv_id)
    await asyncio.wait_for(asyncio.gather(*asyncio.all_tasks() - {asyncio.current_task()},
                                           return_exceptions=True), timeout=2)
    st = db.get_kb_sediment_status(conv_id)
    assert st["status"] == "skipped" and "内容过少" in st["note"]


def test_pending_items_include_sediment_progress():
    """待沉淀明细携带沉淀进度字段（未调度时为 waiting）。"""
    conv_id = db.create_conversation("进度字段测试")["id"]
    db.audit("agent.kb.hit", {"args": {"question": "怎么配HA"}}, conv_id=conv_id)
    item = next((i for i in pks.pending_items() if i["conv_id"] == conv_id), None)
    assert item is not None
    assert item["sediment_status"] == "waiting" and item["sediment_note"] == ""
    db.save_kb_sediment_status(conv_id, "running", "正在提炼知识词条")
    item2 = next((i for i in pks.pending_items() if i["conv_id"] == conv_id), None)
    assert item2["sediment_status"] == "running" and "提炼" in item2["sediment_note"]


# ---------- 录入结果明细 / 排序 / 无设备依赖 ----------

def test_persist_entries_detail_reports_actions():
    """detail 逐条记录新建/合并更新的词条主题，供录入结果向用户说明去向。"""
    entries = [
        {"topic": "NAT64地址转换原理", "category": "最佳实践", "summary": "s", "content_md": "c",
         "key_points": [], "tags": [], "references": []},
        {"topic": "链路探测健康检查配置", "category": "最佳实践", "summary": "s", "content_md": "c",
         "key_points": [], "tags": [], "references": []},
    ]
    detail: list[dict] = []
    saved, updated = pks._persist_entries(entries, conv_id="", detail=detail)
    assert (saved, updated) == (2, 0)
    assert [d["action"] for d in detail] == ["created", "created"]
    # 同主题再次录入：合并更新，明细如实标注
    detail2: list[dict] = []
    saved2, updated2 = pks._persist_entries(entries, conv_id="", detail=detail2)
    assert (saved2, updated2) == (0, 2)
    assert all(d["action"] == "updated" for d in detail2)
    assert {d["topic"] for d in detail2} == {"NAT64地址转换原理", "链路探测健康检查配置"}


def test_list_kb_entries_order_by_updated():
    e1 = db.save_kb_entry(_entry("排序词条A"))
    db.save_kb_entry(_entry("排序词条B"))
    # A 后来被合并更新：updated 排序应置顶，created 排序仍在 B 之后
    with db._connect() as conn:
        conn.execute("UPDATE kb_entries SET updated_at='2030-01-01T00:00:00' WHERE id=?",
                     (e1["id"],))
        conn.execute("UPDATE kb_entries SET created_at='2020-01-01T00:00:00' WHERE id=?",
                     (e1["id"],))
    created = [e["topic"] for e in db.list_kb_entries(order="created", limit=50)]
    updated = [e["topic"] for e in db.list_kb_entries(order="updated", limit=50)]
    assert created.index("排序词条B") < created.index("排序词条A")
    assert updated.index("排序词条A") < updated.index("排序词条B")


@pytest.mark.asyncio
async def test_ingest_summary_names_merged_topics(monkeypatch):
    """录入全部被合并时，结果说明明确列出被更新的词条主题。"""
    from app.agent import tools

    async def fake_ingest(url, note=""):
        return {"status": "ok", "title": "T", "url": url, "generated": 2,
                "saved": 0, "updated": 2,
                "saved_topics": [], "updated_topics": ["AF路由优先级顺序", "AF链路探测"]}

    monkeypatch.setattr(tools.personal_kb_service, "ingest_url", fake_ingest)
    r = await tools._h_ingest_url(None, {"url": "https://a.b/c"}, {})
    assert "未新建词条" in r["_llm_summary"]
    assert "《AF路由优先级顺序》" in r["_llm_summary"] and "《AF链路探测》" in r["_llm_summary"]


def test_compact_result_ingest_preview():
    from app.agent.orchestrator import _compact_result
    preview = _compact_result("ingest_url_to_kb",
                              {"status": "ok", "generated": 2, "saved": 0, "updated": 2})
    assert "新增 0" in preview and "更新 2" in preview


def test_kb_tools_do_not_need_device():
    from app.agent.tools import TOOLS_BY_NAME
    assert TOOLS_BY_NAME["record_to_kb"].needs_device is False
    assert TOOLS_BY_NAME["ingest_url_to_kb"].needs_device is False
    assert TOOLS_BY_NAME["add_device"].needs_device is False
    assert TOOLS_BY_NAME["update_acl_rule"].needs_device is True


@pytest.mark.asyncio
async def test_resume_confirm_kb_tool_skips_device_login(device_id, monkeypatch):
    """知识库沉淀工具执行不依赖设备连接：绑定设备登录失败也不影响录入。"""
    import json as _json
    from app.agent import orchestrator as orch_mod

    async def _boom(dev_id):
        raise RuntimeError("设备登录超时")

    monkeypatch.setattr(orch_mod, "get_client", _boom)
    conv = db.create_conversation("无设备录入测试", device_id=device_id)
    action = db.create_pending_action({
        "id": db.new_id("act_"), "conv_id": conv["id"], "tool_name": "record_to_kb",
        "args_json": _json.dumps({"tool_call_id": "call_1", "args": {"note": "沉淀本次对话"}}),
        "summary": "沉淀本次对话", "status": "pending", "created_at": db.now()})

    events = [ev async for ev in orch_mod.AgentOrchestrator().resume_confirm(
        conv["id"], action["id"], True, device_id)]
    types = [e["type"] for e in events]
    assert "confirm_result" in types and "error" not in types
    assert db.get_pending_action(action["id"])["status"] == "executed"


# ---------- 沉淀提炼稳定性：自动重试 / 流式调用 / 后台串行锁 ----------

@pytest.mark.asyncio
async def test_sediment_auto_retry_on_transient_failure(device_id, monkeypatch):
    """提炼异常（如超时）自动退避重试一次：重试成功即 done，note 不再是 failed。"""
    conv = db.create_conversation("沉淀重试测试")["id"]
    db.audit("agent.kb.hit", {"args": {"question": "q"}}, conv_id=conv)
    monkeypatch.setattr(pks, "SEDIMENT_RETRY_DELAY", 0.0)
    calls = {"n": 0}

    async def fake_generate(c, force=False, since_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Request timed out.")
        return {"status": "ok", "conv_id": c, "generated": 1, "saved": 1, "updated": 0}

    monkeypatch.setattr(pks, "generate_entries_for_conv", fake_generate)
    r = await pks._sediment_with_status(conv)
    assert calls["n"] == 2 and r["saved"] == 1
    st = db.get_kb_sediment_status(conv)
    assert st["status"] == "done" and "重试" not in st["status"]


def _chunk(content, finish=None):
    import types
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=content),
                                       finish_reason=finish)])


@pytest.mark.asyncio
async def test_generate_entries_uses_streaming(device_id, monkeypatch):
    """知识沉淀提炼改为流式调用（stream=True）且 max_tokens 收敛为 3500，词条正常落库。"""
    from types import SimpleNamespace as NS

    topic = f"AF IPv6 支持与开启方式 {db.new_id()[-6:]}"
    entry_json = json.dumps([{
        "topic": topic, "category": "配置方法", "summary": "s", "content_md": "c",
        "key_points": [], "tags": [], "references": [],
        "aliases": [], "related": [],
    }], ensure_ascii=False)

    class _FakeCompletions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)

            async def _gen():
                yield _chunk(entry_json[:20])
                yield _chunk(entry_json[20:], "stop")
            return _gen()

    fake_completions = _FakeCompletions()

    class _FakeLLM:
        chat = NS(completions=fake_completions)

    monkeypatch.setattr(pks, "_llm", lambda: _FakeLLM())

    conv = db.create_conversation("流式提炼测试")["id"]
    db.add_message(conv, "user", {"text": "这个设备是否支持IPv6，如何开启？"})
    db.add_message(conv, "tool", {"tool_call_id": "c1", "name": "search_official_knowledge",
                                  "content": json.dumps({
                                      "source": "zhuge_official_kb", "status": "ok",
                                      "answer": "支持 IPv6，请在 Web 界面开启 IPv6 功能并配置接口地址。",
                                      "references": [{"title": "官方文档"}]}, ensure_ascii=False)})
    db.add_message(conv, "assistant", {"text": "支持。开启方式如下：进入网络配置 → IPv6，启用后配置接口地址与路由。"})
    r = await pks.generate_entries_for_conv(conv)
    assert r["status"] == "ok" and r["saved"] == 1
    assert fake_completions.calls[0]["stream"] is True
    assert fake_completions.calls[0]["max_tokens"] == 8000
    assert fake_completions.calls[0]["top_p"] == 0.95
    assert fake_completions.calls[0]["extra_body"]["thinking"] == {
        "type": "enabled", "clear_thinking": False}
    assert db.get_kb_entry_by_topic(topic) is not None


@pytest.mark.asyncio
async def test_background_llm_lock_serialized():
    """后台串行锁：同一事件循环内多次获取返回同一把锁，且互斥执行。"""
    lock = pks.background_llm_lock()
    assert lock is pks.background_llm_lock()
    order = []

    async def worker(tag):
        async with pks.background_llm_lock():
            order.append(f"{tag}-start")
            await asyncio.sleep(0.01)
            order.append(f"{tag}-end")

    await asyncio.gather(worker("a"), worker("b"))
    assert order == ["a-start", "a-end", "b-start", "b-end"] or \
        order == ["b-start", "b-end", "a-start", "a-end"]


# ---------- 零产出沉淀不滞留待沉淀列表（回归：done 0/0 一直显示） ----------

def test_pending_excludes_zero_yield_and_skipped():
    """最近一次沉淀为零产出（done 0/0）或已判定无沉淀价值（skipped）的会话不再显示；
    失败/进行中的会话保留；会话再次命中（状态重置为排队）时重新出现。"""
    def _mk(tag):
        conv = db.create_conversation(f"零产出测试{tag}")["id"]
        db.audit("agent.kb.hit", {"args": {"question": tag}}, conv_id=conv)
        return conv

    c_zero = _mk("zero")
    db.save_kb_sediment_status(c_zero, "done", "完成：新增 0 条 / 更新 0 条", saved=0)
    c_skip = _mk("skip")
    db.save_kb_sediment_status(c_skip, "skipped", "本次对话无新增沉淀价值")
    c_fail = _mk("fail")
    db.save_kb_sediment_status(c_fail, "failed", "沉淀失败：Request timed out.")
    c_run = _mk("run")
    db.save_kb_sediment_status(c_run, "running", "正在提炼知识词条")

    pending = db.list_pending_kb_convs()
    assert c_zero not in pending and c_skip not in pending     # 零产出/无价值：不显示
    assert c_fail in pending and c_run in pending              # 失败/进行中：保留可重试

    # 再次命中知识库：进度重置为排队，重新出现在待沉淀列表
    db.audit("agent.kb.hit", {"args": {"question": "再问一次"}}, conv_id=c_zero)
    db.save_kb_sediment_status(c_zero, "pending", "排队等待自动沉淀")
    assert c_zero in db.list_pending_kb_convs()


@pytest.mark.asyncio
async def test_generate_empty_entries_returns_skipped_with_log(device_id, monkeypatch, caplog):
    """提炼输出为空（模型判定无价值/无法解析）：返回 skipped 并记录原始输出片段。"""
    from types import SimpleNamespace as NS

    class _FakeCompletions:
        async def create(self, **kwargs):
            async def _gen():
                yield _chunk("本次对话内容与已有知识一致，无需沉淀。", "stop")
            return _gen()

    class _FakeLLM:
        chat = NS(completions=_FakeCompletions())

    monkeypatch.setattr(pks, "_llm", lambda: _FakeLLM())

    conv = db.create_conversation("空产出测试")["id"]
    db.add_message(conv, "user", {"text": "这个设备是否支持IPv6？"})
    db.add_message(conv, "tool", {"tool_call_id": "c1", "name": "search_official_knowledge",
                                  "content": json.dumps({
                                      "source": "zhuge_official_kb", "status": "ok",
                                      "answer": "支持 IPv6，开启方式详见官方文档。",
                                      "references": []}, ensure_ascii=False)})
    db.add_message(conv, "assistant", {"text": "支持 IPv6。"})
    r = await pks.generate_entries_for_conv(conv)
    assert r["status"] == "skipped" and "无新增沉淀价值" in r["reason"]
