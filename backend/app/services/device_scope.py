"""设备域统一解析：global / nd_ 网络设备 / 深信服设备三种上下文的单一来源。

约定（前后端共享，前端常量见 frontend/src/store.js）：
- 全局模式 id 固定为 "global"；
- 网络设备 id 以 "nd_" 前缀；
- 其余为深信服设备（AF/AC/SCP）。
"""
from app import db

GLOBAL_DEVICE_ID = "global"
NETDEV_PREFIX = "nd_"


def device_kind(device_id: str) -> str:
    """解析设备上下文类型：'global' / 'netdev' / 'sangfor'。"""
    if device_id == GLOBAL_DEVICE_ID:
        return "global"
    if str(device_id or "").startswith(NETDEV_PREFIX):
        return "netdev"
    return "sangfor"


def get_device_any(device_id: str) -> dict | None:
    """按 id 取设备记录（global 返回合成上下文）；不存在返回 None。"""
    kind = device_kind(device_id)
    if kind == "global":
        return {"id": GLOBAL_DEVICE_ID, "name": "全局（所有设备）", "type": "global"}
    if kind == "netdev":
        return db.get_netdev_device(device_id)
    return db.get_device(device_id)


def exists(device_id: str) -> bool:
    """设备存在性校验（global 恒为存在）。"""
    if device_kind(device_id) == "global":
        return True
    return get_device_any(device_id) is not None
