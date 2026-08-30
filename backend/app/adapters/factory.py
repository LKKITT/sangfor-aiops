"""设备客户端工厂：按设备记录创建对应 DeviceClient 实例。

- mode=simulator：内置模拟器（进程内 ASGI 直连，无需端口，联调/演示零依赖）
- mode=real：真实设备 REST API（同一套 AfRestClient 代码路径）
"""
import httpx

from app.adapters.af_rest import AfRestClient
from app.adapters.base import DeviceClient
from app.adapters.simulator.app import create_simulator_app
from app.adapters.simulator.state import STATE
from app.config import settings
from app.db import get_device

_simulator_app = None


def simulator_app():
    global _simulator_app
    if _simulator_app is None:
        _simulator_app = create_simulator_app(STATE)
    return _simulator_app


def create_client(device: dict) -> DeviceClient:
    timeout = settings.device_http_timeout
    if device.get("mode") == "simulator":
        # 进程内 ASGI 传输：走完整 HTTP + token 认证链路，但不占端口
        transport = httpx.ASGITransport(app=simulator_app())
        return AfRestClient(
            device_id=device["id"], device_name=device["name"],
            base_url="http://simulator.local", username=device.get("username") or "admin",
            password=device.get("password") or settings.simulator_auth["password"],
            timeout=timeout, transport=transport)
    return AfRestClient(
        device_id=device["id"], device_name=device["name"],
        base_url=device["base_url"], username=device.get("username", ""),
        password=device.get("password", ""), verify_ssl=False, timeout=timeout)


async def get_client(device_id: str) -> DeviceClient:
    device = get_device(device_id)
    if not device:
        raise ValueError(f"设备不存在：{device_id}")
    return create_client(device)
