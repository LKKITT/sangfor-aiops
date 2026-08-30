"""深信服 AF REST API 适配器。

按官方 AF 8.0.x REST API 实现（support.sangfor.com.cn《API 帮助文档》，AF8.0.107 共 1235 个操作）：
- POST /api/v1/namespaces/{ns}/login 用 name/password 换取 token，token 以 Cookie 携带；
- 业务失败码 1003/1012（会话失效）时自动重登一次；
- 列表接口支持 _start/_length 分页；
- 同一客户端可指向真实设备或内置模拟器（端点同构）。
"""
import asyncio
from typing import Any

import httpx

from app.adapters.base import (
    AclRule, ChangeOp, DeviceClient, DeviceError, DeviceStatus,
    InterfaceInfo, NatRule, StaticRoute, UserBinding,
)
from app.config import settings

AUTH_FAIL_CODES = {1003, 1012}


class AfRestClient(DeviceClient):
    device_type = "af"

    def __init__(self, device_id: str, device_name: str, base_url: str,
                 username: str, password: str, verify_ssl: bool = False,
                 timeout: float | None = None, namespace: str = "public",
                 transport: httpx.AsyncBaseTransport | None = None):
        self.device_id = device_id
        self.device_name = device_name
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.namespace = namespace
        self._timeout = timeout or settings.device_http_timeout
        self._client = httpx.AsyncClient(
            base_url=self.base_url, verify=verify_ssl, timeout=self._timeout, transport=transport)
        self._token: str = ""
        self._auth_lock = asyncio.Lock()

    # ---------- 底层 HTTP ----------
    async def _request(self, method: str, path: str, json_body: dict | None = None,
                       allow_relogin: bool = True) -> Any:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Cookie"] = f"token={self._token}"
        try:
            resp = await self._client.request(method, path, json=json_body, headers=headers)
        except httpx.HTTPError as e:
            raise DeviceError(f"设备连接失败：{e.__class__.__name__}: {e}") from e
        if resp.status_code != 200:
            raise DeviceError(f"设备返回 HTTP {resp.status_code}: {resp.text[:200]}", code=resp.status_code)
        try:
            payload = resp.json()
        except ValueError as e:
            raise DeviceError(f"设备响应非 JSON：{resp.text[:200]}") from e
        code = payload.get("code", -1)
        if code in AUTH_FAIL_CODES and allow_relogin:
            async with self._auth_lock:
                if self._token == resp.request.headers.get("cookie", "").replace("token=", ""):
                    await self.login(force=True)
            return await self._request(method, path, json_body, allow_relogin=False)
        if code != 0:
            raise DeviceError(payload.get("message") or f"设备业务错误 code={code}", code=code)
        return payload.get("data")

    # ---------- 会话 ----------
    async def login(self, force: bool = False) -> bool:
        if force:
            self._token = ""
        data = await self._request("POST", f"/api/v1/namespaces/{self.namespace}/login",
                                   {"name": self.username, "password": self.password},
                                   allow_relogin=False)
        try:
            self._token = (data or {}).get("loginResult", {}).get("token", "")
        except AttributeError as e:
            raise DeviceError("登录响应缺少 loginResult.token") from e
        if not self._token:
            raise DeviceError("登录失败：未获取到 token")
        return True

    async def keepalive(self) -> bool:
        await self._request("GET", f"/api/v1/namespaces/{self.namespace}/keepalive")
        return True

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- 状态 ----------
    async def get_status(self) -> DeviceStatus:
        version, summary = await asyncio.gather(
            self._request("GET", f"/namespaces/{self.namespace}/systemversion"),
            self._request("GET", f"/namespaces/{self.namespace}/status/summary"),
        )
        return DeviceStatus(
            sw_version=(version or {}).get("version", "unknown"),
            model=(version or {}).get("model", ""),
            uptime=str((summary or {}).get("uptime", "")),
            cpu_usage=float((summary or {}).get("cpu_usage", 0)),
            memory_usage=float((summary or {}).get("memory_usage", 0)),
            disk_usage=float((summary or {}).get("disk_usage", 0)),
            session_count=int((summary or {}).get("session_count", 0)),
            session_capacity=int((summary or {}).get("session_capacity", 0)) or 1,
            mbuf_usage=float((summary or {}).get("mbuf_usage", 0)),
            ha_status=(summary or {}).get("ha_status", "standalone"),
        )

    async def get_interfaces(self) -> list[InterfaceInfo]:
        data = await self._request("GET", f"/namespaces/{self.namespace}/interfaces",
                                   {"_start": 0, "_length": 10000})
        rows = (data or {}).get("list", []) if isinstance(data, dict) else (data or [])
        out = []
        for r in rows:
            out.append(InterfaceInfo(
                name=r.get("name", ""), zone=r.get("zone", ""), ip=r.get("ip", ""),
                netmask=r.get("netmask", ""), status=r.get("status", "down"),
                speed=r.get("speed", ""), mac=r.get("mac", ""),
                rx_kbps=float(r.get("rx_kbps", 0) or 0), tx_kbps=float(r.get("tx_kbps", 0) or 0),
                comment=r.get("comment", "")))
        return out

    async def get_nat_rules(self) -> list[NatRule]:
        data = await self._request("GET", f"/namespaces/{self.namespace}/nats",
                                   {"_start": 0, "_length": 10000})
        rows = (data or {}).get("list", []) if isinstance(data, dict) else (data or [])
        return [NatRule(**{k: r.get(k, d) for k, d in
                (("id", ""), ("name", ""), ("enabled", True), ("type", "SNAT"), ("src_zone", "any"),
                 ("dst_zone", "any"), ("src_addr", "any"), ("dst_addr", "any"), ("service", "any"),
                 ("translated_addr", ""), ("translated_port", ""), ("hit_count", 0),
                 ("log", True), ("comment", ""))}) for r in rows]

    async def get_acl_rules(self) -> list[AclRule]:
        data = await self._request("GET", f"/namespaces/{self.namespace}/appcontrols/policys",
                                   {"_start": 0, "_length": 10000})
        rows = (data or {}).get("list", []) if isinstance(data, dict) else (data or [])
        return [AclRule(**{k: r.get(k, d) for k, d in
                (("id", ""), ("name", ""), ("enabled", True), ("src_zone", "any"), ("dst_zone", "any"),
                 ("src_addr", "any"), ("dst_addr", "any"), ("service", "any"), ("app", "any"),
                 ("action", "deny"), ("hit_count", 0), ("log", False), ("comment", ""))}) for r in rows]

    async def get_user_bindings(self) -> list[UserBinding]:
        data = await self._request("GET", f"/namespaces/{self.namespace}/userbindings")
        rows = (data or {}).get("list", []) if isinstance(data, dict) else (data or [])
        return [UserBinding(
            id=r.get("id", ""), user=r.get("user", ""), ip=r.get("ip", ""), mac=r.get("mac", ""),
            binding_type=r.get("binding_type", "static"), enabled=bool(r.get("enabled", True)),
            comment=r.get("comment", "")) for r in rows]

    async def get_static_routes(self) -> list[StaticRoute]:
        data = await self._request("GET", f"/namespaces/{self.namespace}/staticroutes/ipv4")
        rows = (data or {}).get("list", []) if isinstance(data, dict) else (data or [])
        return [StaticRoute(
            id=r.get("id", ""), name=r.get("name", ""), dst=r.get("dst", ""), next_hop=r.get("next_hop", ""),
            interface=r.get("interface", ""), distance=int(r.get("distance", 10)),
            enabled=bool(r.get("enabled", True)), comment=r.get("comment", "")) for r in rows]

    # ---------- 变更 ----------
    _PATHS = {
        "nat": "/nats",
        "acl": "/appcontrols/policys",
        "binding": "/userbindings",
        "route": "/staticroutes/ipv4",
    }

    async def apply_change(self, change: ChangeOp) -> dict:
        path = self._PATHS.get(change.resource)
        if not path:
            raise DeviceError(f"不支持的资源类型：{change.resource}")
        base = f"/namespaces/{self.namespace}{path}"
        if change.op == "create":
            data = await self._request("POST", base, change.data)
            return {"ok": True, "message": f"已创建 {change.resource} 记录", "data": data}
        if change.op == "update":
            data = await self._request("PATCH", f"{base}/{change.target_id}", change.data)
            return {"ok": True, "message": f"已更新 {change.resource}#{change.target_id}", "data": data}
        if change.op == "delete":
            await self._request("DELETE", f"{base}/{change.target_id}")
            return {"ok": True, "message": f"已删除 {change.resource}#{change.target_id}", "data": None}
        raise DeviceError(f"不支持的操作：{change.op}")

    # ---------- 配置文件（模拟控制台私有端点，真实设备若端点不可用会抛错并降级） ----------
    async def backup_config_file(self) -> tuple[bytes, str]:
        resp = await self._client.get(
            f"/namespaces/{self.namespace}/configfile/download",
            headers={"Cookie": f"token={self._token}"})
        if resp.status_code != 200:
            raise DeviceError(f"配置文件下载失败 HTTP {resp.status_code}")
        disp = resp.headers.get("content-disposition", "")
        filename = "config.conf"
        if "filename=" in disp:
            filename = disp.split("filename=", 1)[1].strip('"')
        return resp.content, filename

    async def restore_config_file(self, data: bytes) -> dict:
        resp = await self._client.post(
            f"/namespaces/{self.namespace}/configfile/upload",
            content=data, headers={"Cookie": f"token={self._token}",
                                   "Content-Type": "application/octet-stream"})
        if resp.status_code != 200:
            raise DeviceError(f"配置文件上传失败 HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if payload.get("code") != 0:
            raise DeviceError(payload.get("message", "配置恢复被设备拒绝"))
        return payload.get("data") or {}
