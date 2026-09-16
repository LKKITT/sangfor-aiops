"""AF 状态会话数（topsessionnumbers 汇总）与报告体检表格渲染测试。

回归背景：真实 AF 设备状态/报告的会话数恒为 0（未接会话数量排行接口）；
报告体检表描述列取了不存在的 detail 字段恒为空，且不展示涉及的具体策略。
"""
from app.adapters.af_rest import AfRestClient
from app.services.report_generator import render_checkup_section

TOPS_PAYLOAD = {
    "items": [
        {"ip": "20.20.2.120", "sessionNumber": {"total": 27802, "TCP": 24454, "newSession": 37}},
        {"ip": "20.20.2.164", "sessionNumber": {"total": 27750, "TCP": 24363, "newSession": 36}},
        {"ip": "bad", "sessionNumber": None},          # 防御解析：脏数据跳过
        {"ip": "20.20.2.210"},                          # 防御解析：无 sessionNumber
    ]
}


def test_sum_session_totals():
    assert AfRestClient._sum_session_totals(TOPS_PAYLOAD) == 27802 + 27750
    assert AfRestClient._sum_session_totals({}) == 0
    assert AfRestClient._sum_session_totals({"items": None}) == 0


def test_status_parts_populate_session_count():
    """_status_from_parts 输出包含会话总数（取不到时保持 0，不抛异常）。"""
    import asyncio

    class _FakeClient:
        capability_gaps = {}
        namespace = "public"
        _sum_session_totals = staticmethod(AfRestClient._sum_session_totals)

        async def _request(self, method, path, params=None):
            if "topsessionnumbers" in path:
                return TOPS_PAYLOAD
            if "uptimes" in path:
                return {"upTimes": "1455天"}
            return {}   # cpu/memory/disk 走 _num 防御解析

    out = asyncio.run(AfRestClient._status_from_parts(_FakeClient()))
    assert out["session_count"] == 27802 + 27750
    assert out["uptime"] == "1455天"


def test_checkup_section_renders_title_evidence_and_rule_names():
    """体检表渲染风险说明（标题+证据）与涉及策略（ID 按快照映射为名称）。"""
    snapshot = {"acl_rules": [
        {"id": "BA2EE0C51CF04238", "name": "smb"},
        {"id": "77F8D48F89D549AC", "name": "allow"},
    ]}
    checkup = {
        "score": 55, "grade": "存在风险",
        "counts": {"high": 1, "medium": 0, "low": 0}, "auto_fixable": 0,
        "items": [{
            "check_id": "ACL_CONFLICT", "severity": "high", "category": "规则冲突",
            "title": "策略「smb」被「allow」覆盖",
            "evidence": "两条策略动作相反，deny 永不生效",
            "rule_ids": ["BA2EE0C51CF04238", "UNKNOWN_ID"],
            "suggestion": "确认业务意图后调整策略顺序",
            "auto_fix": [],
        }],
    }
    html = render_checkup_section(checkup, snapshot)
    assert "风险说明" in html and "涉及策略" in html          # 新列
    assert "策略「smb」被「allow」覆盖" in html               # 标题进入风险说明
    assert "两条策略动作相反" in html                          # 证据进入风险说明
    assert "smb (BA2EE0C51CF0…)" in html                       # ID 映射为名称
    assert "UNKNOWN_ID" in html                                # 快照中不存在的 ID 原样展示
    assert "detail" not in html                                # 不再取不存在的字段


def test_checkup_section_without_items():
    html = render_checkup_section({"score": 100, "grade": "健康",
                                   "counts": {"high": 0, "medium": 0, "low": 0}}, None)
    assert "100" in html and "<table>" not in html
