"""控制台 AI 分析：回显脱敏、参数校验、LLM JSON 解析与审计（mock，不触网）。"""
import sys

import pytest

sys.path.insert(0, ".")
from fastapi import HTTPException  # noqa: E402

from app import db  # noqa: E402
from app.api import netdev as netdev_api  # noqa: E402


@pytest.fixture()
def dev():
    return db.save_netdev_device({"host": "10.98.0.1", "port": 22, "name": "AI-测试交换机",
                                  "vendor": "h3c", "model": "S5130"})


def test_sanitize_masks_secret_lines():
    text = "login: admin\npassword: SuperSecret123\ncommunity public\nsysname SW-1"
    out = netdev_api.sanitize_terminal_output(text)
    assert "SuperSecret123" not in out and "******" in out
    assert "sysname SW-1" in out


def test_sanitize_truncates_volume_and_lines():
    out = netdev_api.sanitize_terminal_output("line\n" * 5000)
    assert out.count("\n") <= 201 and len(out) <= netdev_api._OUTPUT_MAX_CHARS


@pytest.mark.asyncio
async def test_analyze_404_unknown_device():
    with pytest.raises(HTTPException) as ei:
        await netdev_api.console_analyze(
            netdev_api.ConsoleAnalyzeIn(device_id="nd_not_exist", output="display version"))
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_analyze_400_empty_output(dev):
    with pytest.raises(HTTPException) as ei:
        await netdev_api.console_analyze(netdev_api.ConsoleAnalyzeIn(device_id=dev["id"], output="  \n"))
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_analyze_409_without_llm_key(dev, monkeypatch):
    monkeypatch.setattr(netdev_api, "get_llm_config",
                        lambda: {"api_key": "", "base_url": "", "model": ""})
    with pytest.raises(HTTPException) as ei:
        await netdev_api.console_analyze(
            netdev_api.ConsoleAnalyzeIn(device_id=dev["id"], output="display version"))
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_analyze_parses_llm_json_and_audits(dev, monkeypatch):
    monkeypatch.setattr(netdev_api, "get_llm_config",
                        lambda: {"api_key": "k", "base_url": "http://llm", "model": "test-model"})

    class _FakeClient:
        last_call = {}

        def __init__(self, **kw):
            _FakeClient.last_call = kw
            self.chat = self
            self.completions = self

        async def create(self, **kw):
            _FakeClient.last_call = kw
            payload = '{"analysis": "接口处于 down 状态", "commands": ["display interface brief"]}'
            class _M:
                content = payload
            class _Ch:
                message = _M()
            class _R:
                choices = [_Ch()]
            return _R()

    monkeypatch.setattr(netdev_api, "AsyncOpenAI", _FakeClient)
    r = await netdev_api.console_analyze(
        netdev_api.ConsoleAnalyzeIn(device_id=dev["id"], output="GigabitEthernet0/0 down",
                                    current_command="display interface brief"))
    assert "down" in r["analysis"]
    assert r["commands"] == ["display interface brief"]
    # 审计落库
    logs = db.list_audit(limit=20)
    assert any("netdev.console.ai" in l["action"] for l in logs)
    # 提示词携带设备与厂商上下文
    sent = _FakeClient.last_call["messages"][1]["content"]
    assert "H3C" in sent or "h3c".upper() in sent
