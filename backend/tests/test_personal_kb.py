"""个人知识库测试：词条 CRUD/去重/待沉淀定位/统计 + WIKI 解析 + 无 LLM 降级。"""
import asyncio

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


def test_pending_convs_requires_kb_audit():
    assert db.list_pending_kb_convs() == []
    conv_id = db.create_conversation("测试对话")["id"]
    db.audit("agent.tool.search_official_knowledge", {"args": {}}, conv_id=conv_id)
    assert conv_id in db.list_pending_kb_convs()
    # 沉淀后不再出现在待沉淀列表
    db.save_kb_entry(_entry("某主题", conv=conv_id))
    assert conv_id not in db.list_pending_kb_convs()


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
    db.audit("agent.tool.search_official_knowledge",
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
        db.audit("agent.tool.search_official_knowledge", {"args": {}}, conv_id=c)
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
    assert got2["references"][0]["url"] == "https://a.b/c"
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
