"""深信服 AC（上网行为管理）开放接口适配器。

依据官方《深信服 AC 开放接口》文档（api/Sangfor_AC_API.pdf）与真机（AC 13.x）实测实现：

- 服务端口 9999，路径 /v1/{interface}；
- GET：random/md5 附于 URL（md5 = md5(共享密钥 + random)，密钥在前；random 每次必须唯一，1 小时内不可复用）；
- POST：Content-Type: application/json，random/md5 放在 JSON body 内；删除用 ?_method=DELETE 重载；
- 部分设备固件版本开放接口返回 HTTP 200 但 body 为空（已知问题），适配器须在无数据时保持稳定。

AC 与 AF 核心差异：
- 无接口/NAT/ACL/路由/网络对象/服务等配置类端点
- 有用户/组/绑定/策略/在线用户/流量等管理类端点
- 状态端点：version, cpu-usage, mem-usage, disk-usage, online-user, session-num, throughput
"""
import hashlib
import time
import uuid
from typing import Any

import httpx

from app.adapters.af_rest import VERSION_CACHE_TTL, VERSION_CACHE_TTL_MISS, permissive_ssl_context
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
        self._managed_bindings: list[dict] = []
        # 版本探测缓存（get_status 高频复用；Web 页降级提取耗时，需缓存）
        self._version_cache: str | None = None
        self._version_cached_at: float = 0.0
        # AC 设备能力缺口说明（与 AF 对比）
        self.capability_gaps: dict[str, str] = {
            "interfaces": "AC 无接口列表端点，上网行为管理类设备不管理网络接口配置",
            "nat_rules": "AC 无 NAT 端点，NAT 转换由网关/防火墙负责",
            "acl_rules": "AC 策略为上网/流控策略，非防火墙访问控制策略",
            "static_routes": "AC 无静态路由端点",
            "objects": "AC 无网络对象端点",
            "services": "AC 无自定义服务端点",
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
        """POST：JSON body，random/md5 放 body 内；支持 _method 重载（GET/DELETE）由 URL 参数传递。"""
        body = dict(body or {})
        body.update(self._auth())
        # 从 body 中提取 _method 并放到 URL query string（AC 开放接口规范要求）
        method_in_body = body.pop("_method", None)
        method_param = method_override or method_in_body
        path = f"/v1/{interface}"
        if method_param:
            path += f"?_method={method_param}"
        try:
            resp = await self._client.post(path, json=body)
        except httpx.HTTPError as e:
            raise DeviceError(f"AC 设备连接失败：{e.__class__.__name__}: {e}") from e
        return self._parse(resp, interface)

    @staticmethod
    def _parse(resp: httpx.Response, interface: str) -> Any:
        """解析 AC 开放接口响应，兼容空 body 和多种 content-type。"""
        if resp.status_code == 401:
            raise DeviceError("开放接口认证失败：请检查共享密钥与来源 IP 白名单")
        if resp.status_code != 200:
            raise DeviceError(f"AC 开放接口 /v1/{interface} 返回 HTTP {resp.status_code}")
        # 部分 AC 设备固件返回 HTTP 200 但 body 为空（content-type: application/octet-stream）
        text = resp.text.strip()
        if not text:
            return {}
        try:
            payload = resp.json()
        except ValueError:
            # 非 JSON 响应视为空数据
            return {}
        code = payload.get("code")
        if code is not None and code != 0:
            raise DeviceError(payload.get("message") or f"AC 业务错误 code={code}",
                              code=code)
        return payload.get("data")

    # ---------- 设备能力检查 ----------
    async def _check_endpoint(self, interface: str) -> bool:
        """检查端点是否可用（返回 True=有数据，False=空响应/不可用）。"""
        try:
            data = await self._get(interface)
            if data is None or data == {} or data == "":
                return False
            return True
        except DeviceError:
            return False

    # ---------- DeviceClient 会话 ----------
    async def login(self) -> bool:
        """登录验证：尝试多个状态端点确认连通性。"""
        # 优先尝试 status/version（API 文档标准端点）
        last_error = ""
        for endpoint in ("status/version", "status/cpu-usage", "status/cpu"):
            try:
                await self._get(endpoint)
                return True
            except DeviceError as e:
                msg = str(e)
                last_error = msg
                if "连接失败" in msg or "认证" in msg or "401" in msg:
                    raise
                # 302 重定向表明 URL 不正确（AC 使用 HTTP 端口 9999，非 HTTPS）
                if "302" in msg:
                    raise DeviceError(
                        "AC 设备开放接口地址不正确（收到 302 重定向），请使用 HTTP 端口 9999 "
                        "（如 http://192.168.253.253:9999），而非 HTTPS")
                # 空响应或业务错误继续尝试下一个端点
                continue
        # 所有端点都失败但非连接/认证错误，仍视为可达（设备开放接口可能返回空数据）
        # 但记录最后一个错误供上层参考
        self.capability_gaps["login"] = f"所有端点均不可用，最后错误：{last_error}"
        return True

    async def keepalive(self) -> bool:
        """保活：轻量查询验证可达性。"""
        try:
            await self._get("status/version")
        except DeviceError as e:
            if "连接失败" in str(e) or "认证" in str(e) or "401" in str(e):
                raise
        return True

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- Web 界面版本提取（降级方案） ----------
    async def _get_version_from_web(self) -> str | None:
        """当开放接口 status/version 端点不可用时，从 Web 管理界面提取版本信息。
        尝试 HTTPS（443）和开放接口端口两种方式访问 Web 管理页面。"""
        import re
        import httpx
        from app.adapters.af_rest import permissive_ssl_context

        # 从 base_url 解析 IP 地址（去除端口）
        base = self.base_url.rstrip("/")
        # 提取 IP：http://IP:PORT 或 https://IP:PORT
        ip_match = re.search(r'https?://([^:/]+)', base)
        if not ip_match:
            return None
        ip = ip_match.group(1)

        # 尝试的 URL 列表：HTTPS 管理界面 + 开放接口端口的 HTTP 页面
        urls = [
            f"https://{ip}",                            # 标准 Web 管理界面（443）
            f"https://{ip}/login.php",                   # 类 AF 登录页面
            f"https://{ip}/login",                       # 常见登录路径
            base,                                        # 开放接口端口（可能也提供 Web 页面）
        ]

        async with httpx.AsyncClient(verify=permissive_ssl_context(), timeout=15, follow_redirects=True) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    html = resp.text
                    if not html or len(html) < 100:
                        continue

                    # 模式1：AC 版本号 AC X.X.X.X 或 AC X.X.X Build
                    m = re.search(r'AC[\s]*([\d]+(?:\.[\d]+)+)', html)
                    if m:
                        return f"AC {m.group(1)}"

                    # 模式2：纯版本号紧跟 AC 关键词
                    m = re.search(r'(?:AC|ac|上网行为管理)[^<]{0,30}?([\d]+\.[\d]+(?:\.[\d]+)+)', html)
                    if m:
                        return f"AC {m.group(1)}"

                    # 模式3：afVersion / appVersion（部分 AC 使用 AF 的 Web 框架）
                    m = re.search(r'afVersion\s*:\s*"([^"]+)', html, re.IGNORECASE)
                    if m:
                        return m.group(1)
                    m = re.search(r'appVersion\s*:\s*"([^"]+)', html, re.IGNORECASE)
                    if m:
                        ver = m.group(1).split(" ")[0]
                        return ver

                    # 模式4：data-version 属性
                    m = re.search(r'data-ver(?:sion)?\s*=\s*["\']([^"\']+)["\']', html)
                    if m:
                        return m.group(1).strip()

                    # 模式5：SVG/图片路径中的版本号
                    m = re.search(r'/(AC[\d.]+(?:\.\d+)?)/', html)
                    if m:
                        return m.group(1)

                    # 模式6：JavaScript 变量 version
                    m = re.search(r'(?:var|let|const)\s+(?:version|swVersion|appVersion|ver)\s*[=:]\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
                    if m:
                        return m.group(1).strip()

                    # 模式7：通用 X.X.X 版本号（>= 7.0 且前面有 version/版本 关键词）
                    m = re.search(r'(?:版本|version|ver|software)\s*[：:]\s*([\d]+\.[\d]+(?:\.[\d]+)+)', html, re.IGNORECASE)
                    if m:
                        return f"AC {m.group(1)}"

                    # 模式8：通用版本号（>= 7.0）
                    m = re.search(r'([7-9]\.[\d]+\.[\d]+(?:\.[\d]+)?)', html)
                    if m:
                        return f"AC {m.group(1)}"

                except Exception:
                    continue
        return None

    # ---------- 状态 ----------
    async def _resolve_version(self, _str) -> str:
        """版本解析（带短 TTL 缓存）：开放接口 → Web 管理页降级提取。

        Web 页降级提取耗时明显，且版本号仅在设备升级后变化，结果按 TTL 缓存复用。
        """
        now = time.monotonic()
        if self._version_cache is not None:
            ttl = VERSION_CACHE_TTL if self._version_cache else VERSION_CACHE_TTL_MISS
            if now - self._version_cached_at < ttl:
                return self._version_cache
        version = await _str("status/version", "version", "sw_version", "ver", "appVersion",
                             "afVersion")
        # 如果开放接口无法获取版本，尝试从 Web 管理界面提取
        if not version or version == "unknown":
            try:
                web_ver = await self._get_version_from_web()
                if web_ver:
                    version = web_ver
            except Exception:
                pass
        self._version_cache = version or ""
        self._version_cached_at = now
        return self._version_cache

    async def get_status(self) -> DeviceStatus:
        """Status 接口聚合：版本/CPU/内存/磁盘/在线用户数/会话数。
        端点失败或返回空数据时逐项降级为 0 / unknown。"""
        async def _num(path: str, *keys) -> float:
            try:
                data = await self._get(path)
                if isinstance(data, dict):
                    for k in keys:
                        v = data.get(k)
                        if v not in (None, ""):
                            try:
                                return float(v)
                            except (TypeError, ValueError):
                                continue
                if isinstance(data, (int, float)):
                    return float(data)
            except (DeviceError, ValueError, TypeError):
                pass
            return 0.0

        async def _str(path: str, *keys) -> str:
            try:
                data = await self._get(path)
                if isinstance(data, dict):
                    for k in keys:
                        v = data.get(k)
                        if v not in (None, ""):
                            return str(v)
                if isinstance(data, str):
                    return data
            except (DeviceError, ValueError, TypeError):
                pass
            return ""

        version = await self._resolve_version(_str)
        cpu = await _num("status/cpu-usage", "cpu", "usage", "value", "cpu_usage")
        mem = await _num("status/mem-usage", "mem", "memory", "usage", "value", "mem_usage")
        disk = await _num("status/disk-usage", "disk", "usage", "value", "disk_usage")
        online = int(await _num("status/online-user", "num", "count"))
        sessions = int(await _num("status/session-num", "num", "count", "session_num"))
        # 尝试获取系统运行时间和带宽使用率
        uptime = await _str("status/sys-time", "time", "sys_time")
        bandwidth = await _num("status/bandwidth-usage", "usage", "bandwidth", "value")

        return DeviceStatus(
            sw_version=version or "unknown",
            model="",
            uptime=uptime or "",
            cpu_usage=cpu,
            memory_usage=mem,
            disk_usage=disk,
            session_count=sessions or online,
            session_capacity=0,   # 0=容量未知
            extra={"online_users": online, "bandwidth_usage": bandwidth} if (online or bandwidth) else {},
        )

    # ---------- AC 特有数据采集 ----------
    async def get_online_users(self, keyword: str = "") -> list[dict]:
        """获取在线用户信息（最多返回 100 个）。"""
        try:
            body = {"_method": "GET"}
            if keyword:
                body["search"] = keyword
            data = await self._post("online-users", body)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data") or data.get("users") or []
            return []
        except DeviceError:
            return []

    async def get_net_policies(self) -> list[dict]:
        """获取所有上网策略信息。"""
        try:
            data = await self._get("policy/netpolicy")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data") or data.get("items") or data.get("list") or []
            return []
        except DeviceError:
            return []

    async def get_flux_policies(self) -> list[dict]:
        """获取所有流控策略（通道）信息。"""
        try:
            data = await self._get("policy/fluxpolicy")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data") or data.get("items") or []
            return []
        except DeviceError:
            return []

    async def get_throughput(self) -> dict:
        """获取吞吐量（上行/下行流速，统一换算为 bps）。

        文档 1.10：GET status/throughput 返回 {send, recv, unit}（send=上行/recv=下行）。
        实测部分固件忽略 unit=bits 仍返回 bytes/s，此处统一 ×8 换算为 bps，
        字段规范化为 up_throughput/down_throughput。
        """
        try:
            body = {"_method": "GET", "unit": "bits"}
            data = await self._post("status/throughput", body)
            if isinstance(data, dict):
                unit = str(data.get("unit", "bits")).lower()
                factor = 8.0 if unit == "bytes" else 1.0
                return {"up_throughput": float(data.get("send") or 0) * factor,
                        "down_throughput": float(data.get("recv") or 0) * factor,
                        "unit": "bits"}
            return {}
        except DeviceError:
            return {}

    async def get_app_rank(self, top: int = 10) -> list[dict]:
        """获取应用流量排行。"""
        try:
            body = {"_method": "GET", "top": top}
            data = await self._post("status/app-rank", body)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data") or data.get("items") or []
            return []
        except DeviceError:
            return []

    async def get_user_rank(self, top: int = 10) -> list[dict]:
        """获取用户流量排行。"""
        try:
            body = {"_method": "GET", "top": top}
            data = await self._post("status/user-rank", body)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data") or data.get("items") or []
            return []
        except DeviceError:
            return []

    # ---------- DeviceClient 抽象方法（AC 不支持的返回空列表） ----------
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
        """按关键词（用户名/IP/MAC）查询绑定关系。

        keyword 为空时查询全部；有 keyword 时按其搜索（只读）。
        先查询 bindinfo/user-bindinfo（用户+IP/MAC 绑定），再查 ipmac-bindinfo（纯IP/MAC 绑定），合并去重。
        """
        out: list[UserBinding] = []
        seen: set[str] = set()
        # 有 keyword 时按关键词搜索，否则传空字符串（设备 API 会返回全部）
        searches = [keyword] if keyword else [""]
        for term in searches:
            # 1) 查询 bindinfo/user-bindinfo（用户+IP/MAC 绑定，支持按用户名/IP/MAC搜索）
            try:
                params = {}
                if term:
                    params["search"] = term
                data = await self._get("bindinfo/user-bindinfo", params)
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
                        source="user_bindinfo", comment=str(row.get("desc", ""))))
            except DeviceError as e:
                if "不存在" in str(e) or "no data" in str(e).lower() or "校验" in str(e):
                    pass
                else:
                    raise
            # 2) 再查询 ipmac-bindinfo（纯IP/MAC 绑定），与 user-bindinfo 结果合并去重
            try:
                params = {}
                if term:
                    params["search"] = term
                ipmac_data = await self._get("ipmac-bindinfo", params)
                # ipmac-bindinfo 返回单个对象时包装为列表
                if isinstance(ipmac_data, dict) and "ip" in ipmac_data:
                    ipmac_rows = [ipmac_data]
                elif isinstance(ipmac_data, list):
                    ipmac_rows = ipmac_data
                else:
                    ipmac_rows = (ipmac_data or {}).get("data") or []
                for row in ipmac_rows:
                    if not isinstance(row, dict):
                        continue
                    key = f"{row.get('ip', '')}:{row.get('mac', '')}"
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(UserBinding(
                        id=key, user="", ip=str(row.get("ip", "")), mac=str(row.get("mac", "")),
                        binding_type="ipmac", enabled=True, source="ipmac_bindinfo",
                        comment=str(row.get("desc", ""))))
            except Exception:
                pass  # ipmac-bindinfo 接口可能不存在或出错，安静忽略
        return out

    async def get_ipmac_bindings(self, keyword: str = "") -> list[UserBinding]:
        """按关键词（IP/MAC）查询纯IP/MAC绑定信息（来自 ipmac-bindinfo 接口），与 get_user_bindings 分开使用。"""
        out: list[UserBinding] = []
        if not keyword:
            return out
        try:
            ipmac_data = await self._get("ipmac-bindinfo", {"search": keyword})
            # ipmac-bindinfo 返回单个对象时包装为列表
            if isinstance(ipmac_data, dict) and "ip" in ipmac_data:
                ipmac_rows = [ipmac_data]
            elif isinstance(ipmac_data, list):
                ipmac_rows = ipmac_data
            else:
                ipmac_rows = (ipmac_data or {}).get("data") or []
            for row in ipmac_rows:
                if not isinstance(row, dict):
                    continue
                out.append(UserBinding(
                    id=f"{row.get('ip', '')}:{row.get('mac', '')}",
                    user="", ip=str(row.get("ip", "")), mac=str(row.get("mac", "")),
                    binding_type="ipmac", enabled=True, source="ipmac_bindinfo",
                    comment=str(row.get("desc", ""))))
        except Exception:
            pass  # ipmac-bindinfo 接口可能不存在或出错，安静忽略
        return out

    async def apply_change(self, change: ChangeOp) -> dict:
        if change.resource not in ("binding", "netpolicy", "fluxpolicy"):
            raise DeviceError(f"AC 开放接口仅支持绑定/策略管理，不支持：{change.resource}")
        if change.resource == "binding":
            return await self._apply_binding_change(change)
        raise DeviceError(f"AC 策略变更暂未实现，支持：{change.resource}")

    @staticmethod
    def _to_bool(val) -> bool:
        """将字符串或布尔值转为布尔（兼容 'true'/'false' 字符串和 True/False）。"""
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            return val.strip().lower() == "true"
        return bool(val)

    async def _apply_binding_change(self, change: ChangeOp) -> dict:
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
            body = {"enable": True, "name": name, "addr_type": addr_type, "addr": addr,
                    "desc": d.get("comment", ""),
                    "limitlogon": self._to_bool(d.get("limitlogon", False)),
                    "noauth": {"enable": self._to_bool(d.get("noauth", False)), "expire_time": 0}}
            await self._post("bindinfo/user-bindinfo", body)
            self._managed_bindings.append({"user": name, "ip": ip or addr, "mac": mac})
            return {"ok": True, "message": f"已添加绑定：{name} ← {addr}", "data": {"addr": addr}}
        if change.op == "delete":
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
        """AC 快照：包含绑定关系、上网策略、流控策略、在线用户等 AC 特有数据。"""
        bindings = [b.to_dict() for b in await self.get_user_bindings()]
        net_policies = await self.get_net_policies()
        flux_policies = await self.get_flux_policies()
        online_users = await self.get_online_users()
        throughput = await self.get_throughput()
        app_rank = await self.get_app_rank(5)
        status = await self.get_status()
        return {
            "meta": {"device_id": self.device_id, "device_name": self.device_name,
                     "device_type": self.device_type, "sw_version": status.sw_version,
                     "model": status.model},
            "objects": [], "services": [], "interfaces": [], "static_routes": [],
            "nat_rules": [], "acl_rules": [], "user_bindings": bindings,
            # AC 特有数据
            "ac_net_policies": net_policies,
            "ac_flux_policies": flux_policies,
            "ac_online_users": online_users,
            "ac_throughput": throughput,
            "ac_app_rank": app_rank,
        }