"""第三批 P2 回归：设备域统一（BE-3）、运行快照与耗时统计（ARC-5）、SSE 事件契约（ARC-4）。"""
import json
import pathlib


from app import db
from app.agent import events
from app.services.device_scope import GLOBAL_DEVICE_ID, device_kind, exists, get_device_any


# ---------- BE-3：设备域统一解析 ----------

def test_device_kind_and_exists(device_id):
    assert device_kind(GLOBAL_DEVICE_ID) == "global"
    assert device_kind("nd_sw1") == "netdev"
    assert device_kind(device_id) == "sangfor"
    assert device_kind("") == "sangfor"

    assert exists(GLOBAL_DEVICE_ID) is True
    assert exists("nd_不存在") is False
    assert exists(device_id) is True
    assert exists("dev_不存在") is False


def test_get_device_any_synthetic_global(device_id):
    g = get_device_any(GLOBAL_DEVICE_ID)
    assert g["id"] == "global" and g["type"] == "global"
    assert get_device_any("nd_不存在") is None
    d = get_device_any(device_id)
    assert d and d["id"] == device_id


# ---------- ARC-5：耗时统计与运行快照 ----------

def test_stats_tool_latency_from_audit(device_id):
    db.audit("agent.tool.get_device_status", {"args": {}, "ms": 120.0},
             device_id=device_id, result="ok")
    db.audit("agent.tool.get_nat_rules", {"args": {}, "ms": 30.0},
             device_id=device_id, result="ok")
    stats = db.stats_tool_latency(minutes=60)
    assert stats["count"] >= 2, "合成的两条含 ms 审计应计入统计"
    assert stats["p50_ms"] is not None and stats["p95_ms"] is not None
    assert stats["p95_ms"] >= stats["p50_ms"] >= 0   # 其他用例可能插入更快的模拟器调用，只验证分布性质


def test_health_has_runtime_snapshot(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert resp.headers.get("x-request-id", "").startswith("req_")
    rt = body["runtime"]
    assert set(rt) >= {"uptime_s", "sangfor_devices", "netdev_devices",
                       "pending_actions", "tool_calls_1h"}
    assert isinstance(rt["tool_calls_1h"]["count"], int)


# ---------- ARC-4：SSE 事件契约 ----------

def test_events_sample_validation():
    """orchestrator 实际产出的事件样例必须通过契约校验。"""
    samples = [
        {"type": "meta", "conv_id": "conv_x", "device_id": "dev_1"},
        {"type": "token", "text": "你好"},
        {"type": "tool_call", "name": "get_device_status", "args": {}},
        {"type": "tool_result", "name": "get_device_status", "preview": "CPU 5%"},
        {"type": "confirm_required", "action": {"action_id": "act_1", "title": "t"}},
        {"type": "confirm_result", "action_id": "act_1", "approved": True,
         "result": {}, "safety_backup_id": "bk_1"},
        {"type": "offline_notice", "text": "离线模式"},
        {"type": "cancelled"},
        {"type": "error", "text": "模型调用失败"},
        {"type": "done"},
    ]
    for s in samples:
        events.__dict__  # noqa: B018 —— 确保模块加载
        pyd = _validate(s)
        assert pyd.type == s["type"]


def _validate(s):
    import pydantic
    models = events.AgentEvent.__args__
    errs = []
    for m in models:
        try:
            return m.model_validate(s)
        except pydantic.ValidationError as e:
            errs.append(e)
    raise AssertionError(f"无匹配事件模型: {s} / {errs}")


def test_sse_schema_doc_matches_models():
    """docs/sse-events.schema.json 与 events.py 模型一致（防契约漂移）。"""
    doc = pathlib.Path(__file__).resolve().parent.parent.parent / "docs" / "sse-events.schema.json"
    assert doc.exists(), "schema 文件缺失：运行 python scripts/gen_sse_schema.py"
    on_disk = json.loads(doc.read_text(encoding="utf-8"))
    models = events.AgentEvent.__args__
    fresh = {"oneOf": [{"$ref": f"#/$defs/{m.__name__}"} for m in models],
             "$defs": {m.__name__: m.model_json_schema() for m in models}}
    for m in models:
        fresh["$defs"][m.__name__].pop("title", None)
    assert on_disk["oneOf"] == fresh["oneOf"]
    assert on_disk["$defs"] == fresh["$defs"]


# ---------- N-7：设备响应契约（脱敏单点化） ----------

def test_devices_response_contract(device_id):
    """/api/devices 响应经 DeviceOut 固化：password 恒为掩码、无多余字段泄漏。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    body = client.get("/api/devices").json()
    dev = next(d for d in body if d["id"] == device_id)
    assert dev["password"] == "***"
    assert set(dev) <= {"id", "name", "type", "mode", "base_url", "username",
                        "password", "readonly", "settings_json", "created_at"}
