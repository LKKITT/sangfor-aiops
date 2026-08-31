"""设备客户端工厂：按设备记录创建并缓存 DeviceClient 实例。

- mode=simulator：内置模拟器（进程内 ASGI 直连，无需端口，联调/演示零依赖）
- mode=real：真实设备 REST API（同一套 AfRestClient 代码路径）

真实设备限制 API 并发会话数：按 device_id 缓存已登录客户端并复用 token，
避免每次请求都重新登录导致会话堆积超限；设备配置变更时自动重建。
"""
import asyncio
import httpx

from app.adapters.ac_rest import AcApiClient
from app.adapters.af_rest import AfRestClient
from app.adapters.base import DeviceClient
from app.adapters.simulator.app import create_simulator_app
from app.adapters.simulator.state import STATE
from app.config import settings
from app.db import get_device

_clients: dict[str, DeviceClient] = {}
_client_signatures: dict[str, str] = {}
_client_lock = asyncio.Lock()   # 串行化首次登录：并发 get_client 会各自登录触发设备会话上限

_simulator_app = None


def simulator_app():
    global _simulator_app
    if _simulator_app is None:
        _simulator_app = create_simulator_app(STATE)
    return _simulator_app


def _signature(device: dict) -> str:
    return f"{device.get('mode')}|{device.get('base_url')}|{device.get('username')}|{device.get('password')}"


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
    if device.get("type") == "ac":
        # AC 开放接口：仅共享密钥（password 字段存放密钥），服务端口 9999
        return AcApiClient(
            device_id=device["id"], device_name=device["name"],
            base_url=device.get("base_url") or "http://10.68.5.1:9999",
            shared_key=device.get("password", ""), timeout=timeout)
    return AfRestClient(
        device_id=device["id"], device_name=device["name"],
        base_url=device["base_url"], username=device.get("username", ""),
        password=device.get("password", ""), verify_ssl=False, timeout=timeout)


async def get_client(device_id: str) -> DeviceClient:
    """取该设备的共享客户端（已登录、可复用）。调用方无需也不应关闭它。"""
    device = get_device(device_id)
    if not device:
        raise ValueError(f"设备不存在：{device_id}")
    sig = _signature(device)
    async with _client_lock:   # 并发请求只做一次登录，避免触发设备并发会话限制
        client = _clients.get(device_id)
        if client is None or _client_signatures.get(device_id) != sig:
            if client is not None:
                await client.aclose()
            client = create_client(device)
            await client.login()
            _clients[device_id] = client
            _client_signatures[device_id] = sig
    return client


async def close_all_clients() -> None:
    """应用退出时统一释放（登出+关连接）。"""
    _stop_keepalive()
    for client in list(_clients.values()):
        try:
            await client.aclose()
        except Exception:   # noqa: BLE001
            pass
    _clients.clear()
    _client_signatures.clear()


# ---------------- token 保活：每 3 分钟对已缓存客户端 keepalive，防止会话过期 ----------------

_keepalive_task: asyncio.Task | None = None
KEEPALIVE_INTERVAL = 180


async def _keepalive_loop() -> None:
    import logging
    log = logging.getLogger("sangfor-agent")
    while True:
        await asyncio.sleep(KEEPALIVE_INTERVAL)
        for device_id, client in list(_clients.items()):
            try:
                await client.keepalive()
            except Exception as e:   # noqa: BLE001 —— 保活失败即丢弃缓存，下次使用时重新登录
                _clients.pop(device_id, None)
                _client_signatures.pop(device_id, None)
                log.info("keepalive 失败，已重置会话 device=%s: %s", device_id, e)


def start_keepalive() -> None:
    global _keepalive_task
    if _keepalive_task is None or _keepalive_task.done():
        _keepalive_task = asyncio.create_task(_keepalive_loop())


def _stop_keepalive() -> None:
    global _keepalive_task
    if _keepalive_task is not None:
        _keepalive_task.cancel()
        _keepalive_task = None
