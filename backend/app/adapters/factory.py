"""设备客户端工厂：按设备记录创建并缓存 DeviceClient 实例。

- mode=simulator：内置模拟器（进程内 ASGI 直连，无需端口，联调/演示零依赖）
- mode=real：真实设备 REST API（同一套 AfRestClient 代码路径）

真实设备限制 API 并发会话数：按 device_id 缓存已登录客户端并复用 token，
避免每次请求都重新登录导致会话堆积超限；设备配置变更时自动重建。

可用性保护：
- 锁按设备隔离（per-device lock）：一台设备登录慢/不可达不会阻塞其它设备的请求；
- 登录带独立超时（DEVICE_LOGIN_TIMEOUT，默认 8s），不可达设备快速失败；
- 失败负缓存：登录失败的设备在冷却期（默认 10s）内直接拒绝新请求，避免排队放大。
"""
import asyncio
import time

import httpx

from app.adapters.ac_rest import AcApiClient
from app.adapters.af_rest import AfRestClient
from app.adapters.base import DeviceClient, DeviceError
from app.adapters.simulator.app import create_simulator_app
from app.adapters.simulator.state import STATE
from app.config import settings
from app.db import get_device

_clients: dict[str, DeviceClient] = {}
_client_signatures: dict[str, str] = {}
_client_locks: dict[str, asyncio.Lock] = {}     # per-device：只串行同设备登录
_failed_until: dict[str, float] = {}            # 失败负缓存：device_id -> 冷却截止时间戳
FAIL_COOLDOWN = 10.0                            # 登录失败后的重试冷却（秒）

_simulator_app = None


def simulator_app():
    global _simulator_app
    if _simulator_app is None:
        _simulator_app = create_simulator_app(STATE)
    return _simulator_app


def _device_lock(device_id: str) -> asyncio.Lock:
    """取（或建）该设备的登录锁。单事件循环内 dict 读写原子，无需外层锁。"""
    lock = _client_locks.get(device_id)
    if lock is None:
        lock = _client_locks[device_id] = asyncio.Lock()
    return lock


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
    # 失败负缓存：冷却期内快速失败，避免排队请求反复挂满登录超时
    until = _failed_until.get(device_id)
    if until and time.monotonic() < until:
        raise DeviceError(f"设备「{device.get('name')}」连接失败，{int(until - time.monotonic()) + 1}s 内暂不重试；"
                          f"请检查设备网络可达性")
    sig = _signature(device)
    async with _device_lock(device_id):   # 仅串行同设备登录，不阻塞其它设备
        client = _clients.get(device_id)
        if client is None or _client_signatures.get(device_id) != sig:
            if client is not None:
                await client.aclose()
            client = create_client(device)
            try:
                await asyncio.wait_for(client.login(), timeout=settings.device_login_timeout)
            except asyncio.TimeoutError:
                await _close_quietly(client)
                _mark_failed(device_id)
                raise DeviceError(f"设备「{device.get('name')}」登录超时（>{int(settings.device_login_timeout)}s），"
                                  f"请检查设备网络可达性")
            except DeviceError:
                await _close_quietly(client)
                _mark_failed(device_id)
                raise
            except Exception as e:   # noqa: BLE001 —— 其它连接异常统一转 DeviceError 并负缓存
                await _close_quietly(client)
                _mark_failed(device_id)
                raise DeviceError(f"设备「{device.get('name')}」连接失败：{e}") from e
            _failed_until.pop(device_id, None)
            _clients[device_id] = client
            _client_signatures[device_id] = sig
    return client


def _mark_failed(device_id: str) -> None:
    _failed_until[device_id] = time.monotonic() + FAIL_COOLDOWN


async def _close_quietly(client: DeviceClient) -> None:
    try:
        await client.aclose()
    except Exception:   # noqa: BLE001
        pass


def reset_cache() -> None:
    """清空全部客户端/锁/负缓存状态（测试隔离用；请求路径不要调用）。"""
    _clients.clear()
    _client_signatures.clear()
    _client_locks.clear()
    _failed_until.clear()


async def close_all_clients() -> None:
    """应用退出时统一释放（登出+关连接）。"""
    _stop_keepalive()
    for client in list(_clients.values()):
        try:
            await client.aclose()
        except Exception:   # noqa: BLE001
            pass
    reset_cache()


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
