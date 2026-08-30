"""pytest 公共夹具：隔离的数据目录 + 模拟器设备。"""
import asyncio
import os
import sys
import tempfile
import pathlib

# 在导入 app 之前隔离数据目录
_TMP = tempfile.mkdtemp(prefix="sangfor-agent-test-")
os.environ["SF_DATA_DIR"] = _TMP
os.environ.setdefault("LLM_API_KEY", "")   # 测试不依赖真实 LLM

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from app import db  # noqa: E402
from app.adapters.af_rest import AfRestClient  # noqa: E402
from app.adapters.factory import create_simulator_app  # noqa: E402
from app.adapters.simulator.state import STATE  # noqa: E402
import httpx  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def init_database():
    db.init_db()
    yield


@pytest.fixture()
def device_id():
    device = db.upsert_device({
        "id": db.new_id("dev_"), "name": "测试设备", "type": "af", "mode": "simulator",
        "base_url": "", "username": "admin", "password": "Sangfor@123",
        "readonly": 0, "settings_json": "{}", "created_at": db.now(),
    })
    return device["id"]


@pytest.fixture()
async def client(device_id):
    """指向内置模拟器的 AfRestClient（进程内 ASGI，走完整 HTTP+token 链路）。"""
    from app.db import get_device
    dev = get_device(device_id)
    c = AfRestClient(device_id, dev["name"], "http://simulator.local", "admin", "Sangfor@123",
                     transport=httpx.ASGITransport(app=create_simulator_app(STATE)))
    await c.login()
    yield c
    await c.aclose()
