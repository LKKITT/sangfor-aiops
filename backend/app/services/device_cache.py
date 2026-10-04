"""设备数据进程内 TTL 缓存：可视化端点（状态/配置快照）的读加速层。

边界（与变更安全强相关，勿破坏）：
- 只缓存在 API/服务层：Agent 变更计划生成（tool.prepare）与工具 handler 直调适配器读取
  实时数据，**永不经过本缓存**，脏缓存不会污染 before/after 比对；
- 写后失效：任何配置写操作（确认执行/恢复 apply/设备变更）后必须调用 invalidate(device_id)。
"""
import asyncio
import time
from typing import Any, Awaitable, Callable

# 状态/接口等实时类数据 TTL（秒）
STATUS_TTL = 4.0
# 配置类数据（objects/acl/nat/services/snapshot 等）TTL（秒）
CONFIG_TTL = 30.0


class DeviceCache:
    """按 (device_id, key) 的 TTL 缓存，带 single-flight（同 key 并发加载合并为一次）。"""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str], tuple[float, Any]] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def get_or_load(self, device_id: str, key: str, ttl: float,
                          loader: Callable[[], Awaitable[Any]]) -> Any:
        slot = (device_id, key)
        hit = self._data.get(slot)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1]
        # single-flight：并发请求等待首次加载完成，避免同设备同 key 重复实拉设备
        lock = self._locks.setdefault(slot, asyncio.Lock())
        async with lock:
            hit = self._data.get(slot)
            if hit and time.monotonic() - hit[0] < ttl:
                return hit[1]
            value = await loader()
            self._data[slot] = (time.monotonic(), value)
            return value

    def invalidate(self, device_id: str) -> None:
        """该设备的全部缓存条目失效（写操作执行后调用）。"""
        for slot in [k for k in self._data if k[0] == device_id]:
            self._data.pop(slot, None)
            self._locks.pop(slot, None)

    def invalidate_all(self) -> None:
        self._data.clear()
        self._locks.clear()


device_cache = DeviceCache()
