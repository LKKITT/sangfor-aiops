"""设备适配层抽象：统一的设备数据模型与 DeviceClient 接口。

AF（下一代防火墙）与 AC（上网行为管理）等设备均通过该接口接入，
上层服务（备份/分析/Agent）只依赖抽象，不感知具体设备型号。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class InterfaceInfo:
    name: str
    zone: str                 # 区域：trust / untrust / dmz / ""
    ip: str
    netmask: str
    status: str               # up / down
    speed: str                # 1000M / 100M ...
    mac: str
    rx_kbps: float = 0.0
    tx_kbps: float = 0.0
    comment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NatRule:
    id: str
    name: str
    enabled: bool = True
    type: str = "SNAT"                       # SNAT / DNAT
    src_zone: str = "any"
    dst_zone: str = "any"
    src_addr: str = "any"                    # any / CIDR / 对象名
    dst_addr: str = "any"
    service: str = "any"                     # any / 服务名 / 端口串
    translated_addr: str = ""                # SNAT: 转换后地址; DNAT: 内部服务器地址
    translated_port: str = ""                # 转换后端口，空表示不变
    hit_count: int = 0
    log: bool = True
    comment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class AclRule:
    """AF 应用控制策略（即访问控制策略）。"""
    id: str
    name: str
    enabled: bool = True
    src_zone: str = "any"
    dst_zone: str = "any"
    src_addr: str = "any"
    dst_addr: str = "any"
    service: str = "any"
    app: str = "any"                          # any / 应用名（如 P2P、视频）
    action: str = "allow"                     # allow / deny
    hit_count: int = 0
    log: bool = False
    comment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class UserBinding:
    """IP-MAC 绑定（AF 绑定关系 / AC 用户绑定）。"""
    id: str
    user: str
    ip: str
    mac: str
    binding_type: str = "static"              # static / dynamic
    enabled: bool = True
    comment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StaticRoute:
    id: str
    name: str = ""
    dst: str = "0.0.0.0/0"
    next_hop: str = ""
    interface: str = ""
    distance: int = 10
    enabled: bool = True
    comment: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DeviceStatus:
    sw_version: str
    model: str
    uptime: str
    cpu_usage: float                          # 0-100
    memory_usage: float
    disk_usage: float
    session_count: int
    session_capacity: int
    mbuf_usage: float = 0.0                   # AF 8.0.85 已知 mbuf 占满问题，专门监控
    ha_status: str = "standalone"             # standalone / master / slave
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ChangeOp:
    """结构化变更操作，用于两阶段确认与恢复回放。"""
    op: str                                   # create / update / delete
    resource: str                             # nat / acl / binding / route / interface
    target_id: str = ""
    data: dict = field(default_factory=dict)  # create/update 的字段
    current: dict = field(default_factory=dict)  # update/delete 变更前状态（用于展示 diff 与回滚）


class DeviceClient(ABC):
    """设备客户端抽象。实现方：AfRestClient（真实设备/独立模拟器）、内置模拟器。"""

    device_id: str = ""
    device_name: str = ""
    device_type: str = "af"

    # ---- 会话 ----
    @abstractmethod
    async def login(self) -> bool: ...

    @abstractmethod
    async def keepalive(self) -> bool: ...

    # ---- 只读查询 ----
    @abstractmethod
    async def get_status(self) -> DeviceStatus: ...

    @abstractmethod
    async def get_interfaces(self) -> list[InterfaceInfo]: ...

    @abstractmethod
    async def get_nat_rules(self) -> list[NatRule]: ...

    @abstractmethod
    async def get_acl_rules(self) -> list[AclRule]: ...

    @abstractmethod
    async def get_user_bindings(self) -> list[UserBinding]: ...

    @abstractmethod
    async def get_static_routes(self) -> list[StaticRoute]: ...

    # ---- 配置快照 ----
    async def snapshot_config(self) -> dict:
        """拉取关键配置形成结构化快照（备份/恢复/分析/可视化的统一数据源）。"""
        interfaces, routes = await self.get_interfaces(), await self.get_static_routes()
        nat, acl = await self.get_nat_rules(), await self.get_acl_rules()
        bindings = await self.get_user_bindings()
        status = await self.get_status()
        return {
            "meta": {
                "device_id": self.device_id,
                "device_name": self.device_name,
                "device_type": self.device_type,
                "sw_version": status.sw_version,
                "model": status.model,
            },
            "interfaces": [i.to_dict() for i in interfaces],
            "static_routes": [r.to_dict() for r in routes],
            "nat_rules": [n.to_dict() for n in nat],
            "acl_rules": [a.to_dict() for a in acl],
            "user_bindings": [b.to_dict() for b in bindings],
        }

    # ---- 变更（写操作，均需经护栏确认后调用） ----
    @abstractmethod
    async def apply_change(self, change: ChangeOp) -> dict:
        """执行单条变更操作，返回 {ok, message, data}。"""

    # ---- 配置文件备份（尽力而为） ----
    async def backup_config_file(self) -> tuple[bytes, str]:
        """下载设备配置文件（.conf/.bcf，私有格式）。默认不支持则抛 NotImplementedError。"""
        raise NotImplementedError("该设备类型不支持 API 配置文件下载")

    async def restore_config_file(self, data: bytes) -> dict:
        raise NotImplementedError("该设备类型不支持 API 配置文件恢复")


class DeviceError(Exception):
    """设备通信/业务错误，message 面向用户展示。"""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code or -1


def find_rule(rules: list[dict], rule_id: str) -> Optional[dict]:
    for r in rules:
        if str(r.get("id")) == str(rule_id):
            return r
    return None
