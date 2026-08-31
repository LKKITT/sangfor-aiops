"""深信服 AC（上网行为管理）开放接口适配器。

依据官方《深信服 AC 开放接口》文档（api/Sangfor_AC_API.pdf）与真机（AC 13.x）实测实现：

- 服务端口 9999，路径 /v1/{interface}；
- GET：random/md5 附于 URL（md5 = md5(共享密钥 + random)，密钥在前；random 每次必须唯一，1 小时内不可复用）；
- POST：Content-Type: application/json，random/md5 放在 JSON body 内；删除用 ?_method=DELETE 重载；
- 绑定关系 BindInfo：
  * GET  /v1/bindinfo/user-bindinfo?search=VALUE   查询用户和 IP/MAC 绑定（按用户名/IP/MAC 搜索）
  * GET  /v1/ipmac-bindinfo?search=VALUE           查询 IPMAC 绑定
  * POST /v1/bindinfo/user-bindinfo                增加（enable/name/addr_type(ip|mac|ipmac)/addr/limitlogon/noauth）
  * POST /v1/bindinfo/user-bindinfo?_method=DELETE 删除（{"addr": "1.1.1.1"}，以 IP 为准）
  * POST /v1/bindinfo/ipmac-bindinfo               增加 IPMAC 绑定（ip/mac/desc）
  * POST /v1/bindinfo/ipmac-bindinfo?_method=DELETE 删除（{"ip": "1.1.1.1"}）
- 用户：POST /v1/user 添加（name 必填，扩展属性 bind_cfg 为数组 [{ip,mac,out_time,bindgoal,desc}]）；
  POST /v1/user?_method=DELETE 删除（{"name": ...}）；GET /v1/user?name=NAME 查询详情；
- 状态类：GET /v1/status/...（online-user、cpu、memory 等，按文档 Status 接口）。
"""
import asyncio
import hashlib
import uuid
from typing import Any

import httpx

from app.adapters.af_rest import permissive_ssl_context
from app.adapters.base import (
    AclRule, ChangeOp, DeviceClient, DeviceError, DeviceStatus,
    InterfaceInfo, NatRule, NetworkObject, ServiceConfig, StaticRoute, UserBinding,
)
from app.config import settings


class AcApiClient(DeviceClient):
    device_type = "ac"

    def __init__(self, device_id: str, device_name: str, base_url: str,
                 shared_key: str, timeout: float | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.device_id = device_id
        self.device_name = device_name
        self.base_url = base_url.rstrip("/")
        self.shared_key = shared_key
        self._timeout = timeout or settings.device_http_timeout
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=self._timeout, transport=transport,
            verify=permissive_ssl_context())
        self._managed_bindings: list[dict] = []   # 本适配器创建的绑定（仅对这些数据做查询/删除）
        self.capability_gaps: dict[str, str] = {
            "interfaces": "AC 配置类对象不经开放接口（开放接口覆盖 状态/用户/绑定/策略/在线用户）",
            "nat_rules": "AC 配置类对象不经开放接口",
            "acl_rules": "AC 配置类对象不经开放接口",
            "static_routes": "AC 配置类对象不经开放接口",
            "objects": "AC 配置类对象不经开放接口",
            "services": "AC 配置类对象不经开放接口",
        }

    # ---------- 开放接口底层 ----------
    def _auth(self) -> dict:
        random = uuid.uuid4().hex     # 每次必须唯一（同一 random 1 小时内只能用 1 次）
        return {"random": random, "md5": hashlib.md5((self.shared_key + random).encode()).hexdigest()}

    async def _get(self, interface: str, params: dict | None = None) -> Any:
        p = self._auth()
        if params:
            p.update(params)
        try:
            resp = await self._client.get(f"/v1/{interface}", params=p)
        except httpx.HTTPError as e:
            raise DeviceError(f"AC 设备连接失败：{e.__class__.__name__}: {e}") from e
        return self._parse(resp, interface)

    async def _post(self, interface: str, body: dict, method_override: str | None = None) -> Any:
        """POST：JSON body，random/md5 放 body 内；删除用 method_override='DELETE'（?_method=DELETE）。"""
        body = dict(body or {})
        body.update(self._auth())
        path = f"/v1/{interface}" + ("?_method=DELETE" if method_override == "DELETE" else "")
        try:
            resp = await self._client.post(path, json=body)
        except httpx.HTTPError as e:
            raise DeviceError(f"AC 设备连接失败：{e.__class__.__name__}: {e}") from e
        return self._parse(resp, interface)

    @staticmethod
    def _parse(resp: httpx.Response, interface: str) -> Any:
        if resp.status_code == 401:
            raise DeviceError("开放接口认证失败：请检查共享密钥与来源 IP 白名单")
        if resp.status_code != 200:
            raise DeviceError(f"AC 开放接口 /v1/{interface} 返回 HTTP {resp.status_code}")
        try:
            payload = resp.json()
        except ValueError:
            return {}
        if payload.get("code") != 0:
            raise DeviceError(payload.get("message") or f"AC 业务错误 code={payload.get('code')}",
                              code=payload.get("code"))
        return payload.get("data")

    # ---------- DeviceClient 能力 ----------
    async def login(self) -> bool:
        try:
            await self._get("status/cpu")
        except DeviceError as e:
            # 端点存在但业务报错（如数据为空）不影响连通判定；认证失败才向上抛
            if "认证" in str(e) or "401" in str(e):
                raise
        return True

    async def keepalive(self) -> bool:
        # 开放接口无会话概念，签名随请求携带；轻量查询验证可达性
        try:
            await self._get("status/cpu")
        except DeviceError as e:
            if "认证" in str(e) or "401" in str(e):
                raise
        return True

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_status(self) -> DeviceStatus:
        """Status 接口聚合：版本/CPU/内存/磁盘/在线用户数（端点失败逐项降级）。"""
        async def opt(path, *keys):
            try:
                data = await self._get(path)
                if isinstance(data, dict):
                    for k in keys:
                        if data.get(k) not in (None, ""):
                            try:
                                return float(data[k])
                            except (TypeError, ValueError):
                                continue
                if isinstance(data, (int, float)):
                    return float(data)
            except DeviceError:
                pass
            return 0.0

        version = ""
        try:
            v = await self._get("status/version")
            version = str(v.get("version") or v) if isinstance(v, dict) else str(v)
        except DeviceError:
            pass
        cpu = await opt("status/cpu-usage", "cpu", "usage", "value", "cpu_usage")
        mem = await opt("status/mem-usage", "mem", "memory", "usage", "value", "mem_usage")
        disk = await opt("status/disk-usage", "disk", "usage", "value", "disk_usage")
        online = 0
        try:
            ou = await self._get("status/online-user")
            if isinstance(ou, dict):
                online = int(ou.get("num") or ou.get("count") or 0)
            elif isinstance(ou, (int, float)):
                online = int(ou)
        except DeviceError:
            pass
        return DeviceStatus(sw_version=version or "AC（开放接口）", model="",
                            uptime="", cpu_usage=cpu, memory_usage=mem, disk_usage=disk,
                            session_count=online, session_capacity=0)   # 0=容量未知，分析器跳过会话水位检查

    async def get_interfaces(self) -> list[InterfaceInfo]:
        return []
    async def get_nat_rules(self) -> list[NatRule]:
        return []
    async def get_acl_rules(self) -> list[AclRule]:
        return []
    async def get_static_routes(self) -> list[StaticRoute]:
        return []
    async def get_network_objects(self) -> list[NetworkObject]:
        return []
    async def get_services(self) -> list[ServiceConfig]:
        return []

    # ---------- 绑定关系（BindInfo） ----------
    async def get_user_bindings(self, keyword: str = "") -> list[UserBinding]:
        """按关键词（用户名/IP/MAC）查询绑定关系（官方 search 必填，无法全量枚举）。

        keyword 为空时返回本适配器创建过的测试绑定；有 keyword 时按其搜索（只读）。
        """
        out: list[UserBinding] = []
        seen: set[str] = set()
        searches = [keyword] if keyword else [m["ip"] for m in self._managed_bindings]
        for term in searches:
            try:
                data = await self._get("bindinfo/user-bindinfo", {"search": term})
            except DeviceError as e:
                if "不存在" in str(e) or "no data" in str(e).lower() or "校验" in str(e):
                    continue
                raise
            rows = data if isinstance(data, list) else (data or {}).get("data") or []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                key = f"{row.get('name')}:{row.get('addr')}"
                if key in seen:
                    continue
                seen.add(key)
                addr, addr_type = str(row.get("addr", "")), str(row.get("addr_type", "ip"))
                ip = mac = ""
                if addr_type == "ipmac" and "+" in addr:
                    ip, mac = addr.split("+", 1)
                elif addr_type == "ip":
                    ip = addr
                else:
                    mac = addr
                out.append(UserBinding(
                    id=key, user=str(row.get("name", "")), ip=ip, mac=mac,
                    binding_type=addr_type, enabled=bool(row.get("enable", True)),
                    comment=str(row.get("desc", ""))))
        return out

    async def apply_change(self, change: ChangeOp) -> dict:
        if change.resource != "binding":
            raise DeviceError(f"AC 开放接口仅支持绑定关系管理（BindInfo），不支持：{change.resource}")
        d = change.data or {}
        name = d.get("user") or d.get("name") or ""
        ip, mac = str(d.get("ip", "")), str(d.get("mac", ""))
        if change.op == "create":
            if not name:
                raise DeviceError("缺少用户名（user）")
            if not ip and not mac:
                raise DeviceError("缺少绑定对象（ip / mac 至少一项）")
            addr = f"{ip}+{mac}" if (ip and mac) else (ip or mac)
            addr_type = "ipmac" if (ip and mac) else ("ip" if ip else "mac")
            # 语义（官方文档 4.5）：limitlogon=true 限制登录启用；noauth.enable=true 免认证启用；
            # noauth.expire_time=0 永不过期（默认永久有效）
            body = {"enable": True, "name": name, "addr_type": addr_type, "addr": addr,
                    "desc": d.get("comment", ""),
                    "limitlogon": bool(d.get("limitlogon", False)),
                    "noauth": {"enable": bool(d.get("noauth", False)), "expire_time": 0}}
            await self._post("bindinfo/user-bindinfo", body)
            self._managed_bindings.append({"user": name, "ip": ip or addr, "mac": mac})
            return {"ok": True, "message": f"已添加绑定：{name} ← {addr}", "data": {"addr": addr}}
        if change.op == "delete":
            # 删除以 IP 为准（文档：绑定对象关联以 ip 为准）
            addr = d.get("ip") or ip
            if not addr:
                managed = next((m for m in self._managed_bindings
                                if m["user"] == name or m["mac"] == mac), None)
                addr = managed["ip"] if managed else ""
            if not addr:
                raise DeviceError("缺少删除目标（ip）")
            await self._post("bindinfo/user-bindinfo", {"addr": addr}, method_override="DELETE")
            self._managed_bindings = [m for m in self._managed_bindings if m["ip"] != addr]
            return {"ok": True, "message": f"已删除绑定：{addr}", "data": {"addr": addr}}
        if change.op == "update":
            raise DeviceError(
                "绑定修改请按文档 2.4 修改用户信息的 bind_cfg（需 orig_ip/orig_mac 指示原绑定）；"
                "或先删除后新增")
        raise DeviceError(f"不支持的操作：{change.op}")

    async def delete_test_user(self, name: str) -> dict:
        """删除本适配器测试创建的用户（POST /v1/user?_method=DELETE）。"""
        data = await self._post("user", {"name": name}, method_override="DELETE")
        return {"ok": True, "message": f"已删除用户 {name}", "data": data}

    async def snapshot_config(self) -> dict:
        """AC 开放接口场景：快照 = 绑定关系数据。"""
        bindings = [b.to_dict() for b in await self.get_user_bindings()]
        return {
            "meta": {"device_id": self.device_id, "device_name": self.device_name,
                     "device_type": self.device_type, "sw_version": "AC（开放接口）", "model": ""},
            "objects": [], "services": [], "interfaces": [], "static_routes": [],
            "nat_rules": [], "acl_rules": [], "user_bindings": bindings,
        }
