"""深信服 AF REST API 适配器。

按官方 AF 8.0.x REST API 实现（support.sangfor.com.cn《API 帮助文档》，AF8.0.107 共 1235 个操作）：
- POST /api/v1/namespaces/{ns}/login 用 name/password 换取 token，token 以 Cookie 携带；
- 业务失败码 1003/1012（会话失效）时自动重登一次；
- 列表接口支持 _start/_length 分页；
- 同一客户端可指向真实设备或内置模拟器（端点同构）。
- 设备 HTTPS 普遍使用旧密码套件（如 TLS1.2 + AES256-SHA）：关闭证书校验并放宽 SSL 安全等级。
"""
import asyncio
import ssl
from typing import Any

import httpx

from app.adapters.base import (
    AclRule, ChangeOp, DeviceClient, DeviceError, DeviceStatus,
    InterfaceInfo, NatRule, NetworkObject, ServiceConfig, StaticRoute, UserBinding,
)
from app.config import settings

AUTH_FAIL_CODES = {1003, 1012}
# HTTP 状态码标记会话失效，需重登（设备返回 401=未授权，404="request error" 也代表token过期）
HTTP_AUTH_CODES = {401, 404}
# API 文档规定 _length 最大 200（AF 8.0.48 传 10000 会返回 code=1001）
MAX_PAGE_LENGTH = 200


def permissive_ssl_context() -> ssl.SSLContext:
    """兼容网络设备老旧 TLS 配置：不校验证书 + SECLEVEL=0 允许旧密码套件。"""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ctx.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED
    except ssl.SSLError:   # 旧 OpenSSL 无 SECLEVEL 概念时保持默认
        pass
    return ctx


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
            base_url=self.base_url, timeout=self._timeout, transport=transport,
            verify=permissive_ssl_context() if not verify_ssl else True)
        self._token: str = ""
        self._login_data: dict = {}
        self._auth_lock = asyncio.Lock()
        # 真实设备形态标记：非进程内 ASGI 传输即真实设备（写操作需翻译为设备原生格式）
        self.real_shape = transport is None
        # 可选能力端点的降级记录（真实设备型号/版本差异时可见）
        self.capability_gaps: dict[str, str] = {}

    def _optional_list(self, path: str):
        """可选端点装饰器：失败时返回空列表并记录能力缺口，不阻塞快照/体检主流程。"""

        async def wrapper():
            try:
                return await self._request("GET", path, {"_start": 0, "_length": MAX_PAGE_LENGTH})
            except DeviceError as e:
                key = path.rstrip("/").split("/")[-1]
                self.capability_gaps[key] = str(e)
                return {"list": []}
        return wrapper

    # ---------- 底层 HTTP ----------
    async def _request(self, method: str, path: str, json_body: dict | None = None,
                       allow_relogin: bool = True) -> Any:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Cookie"] = f"token={self._token}"
        # GET 请求用 params 传递查询参数，POST/PATCH/DELETE 用 json 传递请求体
        if method.upper() == "GET":
            request_kwargs = {"params": json_body}
        else:
            request_kwargs = {"json": json_body}
        try:
            resp = await self._client.request(method, path, **request_kwargs, headers=headers)
        except httpx.HTTPError as e:
            raise DeviceError(f"设备连接失败：{e.__class__.__name__}: {e}") from e
        if resp.status_code != 200:
            # HTTP 401/404 表示会话失效（设备返回 404 "request error" 也代表token过期），触发重登
            # 检查响应体是否包含 "request error" 来确认是会话失效而非真正的端点不存在
            should_relogin = (
                resp.status_code in HTTP_AUTH_CODES
                and allow_relogin
                and self._token
                and ("request error" in resp.text.lower() or resp.status_code == 401)
            )
            if should_relogin:
                async with self._auth_lock:
                    if self._token == resp.request.headers.get("cookie", "").replace("token=", ""):
                        await self.login(force=True)
                return await self._request(method, path, json_body, allow_relogin=False)
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
            # 非致命业务错误（如端点不存在 code=1002、内部错误 code=1007）不抛异常，
            # 让调用方通过 _optional_list 或 try-except 自行决定降级策略
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
        # 保存登录响应中的版本信息（部分 AF 版本 systemversion 端点不可用时可作为降级方案）
        self._login_data = data or {}
        return True

    async def keepalive(self) -> bool:
        await self._request("GET", f"/api/v1/namespaces/{self.namespace}/keepalive")
        return True

    async def aclose(self) -> None:
        # 真实设备限制 API 并发会话数：关闭前尽力登出释放会话，避免堆积超限
        try:
            if self._token:
                await self._request("POST", f"/api/v1/namespaces/{self.namespace}/logout",
                                    allow_relogin=False)
        except Exception:   # noqa: BLE001 —— 登出失败不影响关闭
            pass
        self._token = ""
        await self._client.aclose()

    # ---------- 状态 ----------
    async def _get_version_safe(self) -> dict:
        """安全获取版本信息。AF 8.0.45/8.0.48 等设备 systemversion 返回 code=1007，
        依次尝试多种方案获取版本信息。"""
        # 尝试 systemversion 端点（无参数）
        try:
            return await self._request("GET", f"/api/v1/namespaces/{self.namespace}/systemversion")
        except DeviceError:
            pass
        # 尝试 systemversion?filter=ALL（部分设备需要此参数）
        try:
            return await self._request("GET", f"/api/v1/namespaces/{self.namespace}/systemversion",
                                       {"filter": "ALL"})
        except DeviceError:
            pass
        # 尝试 /api/v1/namespaces/public/softwareversion（部分 AF 版本有此端点）
        try:
            return await self._request("GET", f"/api/v1/namespaces/{self.namespace}/softwareversion")
        except DeviceError:
            pass
        # 尝试从已保存的登录响应中提取版本信息
        if self._login_data:
            lr = self._login_data.get("loginResult") or {}
            if lr.get("version") or lr.get("sw_version"):
                return {"version": lr.get("version") or lr.get("sw_version", ""),
                        "model": lr.get("model", "")}
        # 尝试从设备Web界面提取版本信息（部分AF版本API不可用但Web界面含版本）
        try:
            web_ver = await self._get_version_from_web()
            if web_ver:
                return web_ver
        except Exception:
            pass
        return {}

    async def _get_version_from_web(self) -> dict | None:
        """从设备Web管理界面提取版本信息（备用方案）。
        部分AF版本（如8.0.48）systemversion API返回内部错误，但Web界面JS中包含版本信息。
        使用独立 HTTP 客户端（避免 base_url 解析问题），优先尝试 login.php 页面。"""
        import re
        import urllib.parse
        # 使用独立客户端，避免 self._client 的 base_url 导致URL解析异常
        async with httpx.AsyncClient(
            verify=permissive_ssl_context(), timeout=30, follow_redirects=True,
        ) as client:
            urls = [
                self.base_url.rstrip("/") + "/login.php",
                self.base_url.rstrip("/") + "/",
            ]
            for url in urls:
                try:
                    resp = await client.get(url)
                    html = resp.text
                    # 跳过空内容或非 HTML 响应
                    if not html or len(html) < 50:
                        continue
                    # 模式1：HTML元素属性 data-version 或 data-ver
                    m = re.search(r'data-ver(?:sion)?\s*=\s*["\']([^"\']+)["\']', html)
                    if m:
                        return {"version": m.group(1).strip(), "model": ""}
                    # 模式2：HTML元素内容含版本号，如 <span class="version">8.0.48</span>
                    m = re.search(r'<[^>]+(?:version|ver|sw_ver|swVersion)[^>]*>([^<]+)</', html, re.IGNORECASE)
                    if m:
                        txt = m.group(1).strip()
                        vm = re.search(r'AF?[\s]*([\d]+(?:\.[\d]+)+)', txt, re.IGNORECASE)
                        if vm:
                            return {"version": f"AF {vm.group(1)}", "model": ""}
                        vm = re.search(r'([\d]+\.[\d]+(?:\.[\d]+)+)', txt)
                        if vm:
                            return {"version": f"AF {vm.group(1)}", "model": ""}
                    # 模式3：JavaScript 变量/对象属性 afVersion:"AF X.X.X"
                    m = re.search(r'afVersion\s*:\s*"AF\s+([\d.]+)', html, re.IGNORECASE)
                    if m:
                        return {"version": f"AF {m.group(1)}", "model": ""}
                    # 模式4：JavaScript 变量 version = "AF8.0.48.895"
                    m = re.search(r'(?:var|let|const)\s+(?:version|swVersion|appVersion|ver)\s*[=:]\s*["\']([^"\']+)["\']', html, re.IGNORECASE)
                    if m:
                        return {"version": m.group(1).strip(), "model": ""}
                    # 模式5：appVersion 字段
                    m = re.search(r'appVersion\s*:\s*"([^"]+)"', html, re.IGNORECASE)
                    if m:
                        ver = m.group(1).split(" ")[0]
                        return {"version": ver, "model": ""}
                    # 模式6：SVG/图片路径中的版本号，如 /AF8.0.48/
                    m = re.search(r'/(AF[\d.]+(?:\.\d+)?)/', html)
                    if m:
                        return {"version": m.group(1), "model": ""}
                    # 模式7：版本号文本 AF 8.0.48 或 AF8.0.48
                    m = re.search(r'AF[\s]*([\d]+(?:\.[\d]+){2,})', html)
                    if m:
                        return {"version": f"AF {m.group(1)}", "model": ""}
                    # 模式8：纯数字版本号 X.X.X.X 紧跟在"版本"/"version"等关键词后
                    m = re.search(r'(?:版本|version|ver|software)\s*[：:]\s*([\d]+\.[\d]+(?:\.[\d]+)+)', html, re.IGNORECASE)
                    if m:
                        return {"version": f"AF {m.group(1)}", "model": ""}
                    # 模式9：通用版本号匹配（页面中任何 X.X.X 格式的数字，且 AF 设备通常版本号 >= 7.0）
                    m = re.search(r'([7-9]\.[\d]+\.[\d]+(?:\.[\d]+)?)', html)
                    if m:
                        return {"version": f"AF {m.group(1)}", "model": ""}
                    # 如果页面是混淆的，尝试解码 XOR 混淆
                    func_match = re.search(r'(?:var\s+)?(\w+)\s*=\s*function\s*\(\s*str\s*,\s*key\s*\)', html)
                    if not func_match:
                        continue
                    func_name = func_match.group(1)
                    key_match = re.search(r'var\s+key\s*=\s*\[([^\]]+)\]', html)
                    if not key_match:
                        continue
                    keys = [int(k.strip()) for k in key_match.group(1).split(',')]
                    call_match = re.search(re.escape(func_name) + r"\s*\(\s*'([0-9A-F]+)'", html)
                    if not call_match:
                        continue
                    hex_data = call_match.group(1)
                    result_parts = []
                    for i in range(0, len(hex_data), 2):
                        byte_val = int(hex_data[i:i+2], 16)
                        key_idx = (i // 2) % len(keys)
                        decoded = byte_val ^ keys[key_idx]
                        result_parts.append('%' + format(decoded, '02X'))
                    decoded_text = urllib.parse.unquote(''.join(result_parts))
                    m = re.search(r'afVersion\s*:\s*"AF\s+([\d.]+)', decoded_text)
                    if m:
                        return {"version": f"AF {m.group(1)}", "model": ""}
                    m = re.search(r'appVersion\s*:\s*"([^"]+)', decoded_text)
                    if m:
                        ver = m.group(1).split(" ")[0]
                        return {"version": ver, "model": ""}
                    # 通用版本号匹配（X.X.X 格式，版本号 >= 7.0）
                    m = re.search(r'([7-9]\.[\d]+\.[\d]+(?:\.[\d]+)?)', decoded_text)
                    if m:
                        return {"version": f"AF {m.group(1)}", "model": ""}
                except Exception:
                    continue
        return None

    async def get_status(self) -> DeviceStatus:
        version = await self._get_version_safe()
        try:
            summary = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/status/summary") or {}
        except DeviceError:
            summary = await self._status_from_parts()
        ver = version or {}
        sw = ver.get("version") or ver.get("full") or "unknown"
        return DeviceStatus(
            sw_version=str(sw),
            model=str(ver.get("model", "")),
            uptime=str(summary.get("uptime", "")),
            cpu_usage=float(summary.get("cpu_usage", 0) or 0),
            memory_usage=float(summary.get("memory_usage", 0) or 0),
            disk_usage=float(summary.get("disk_usage", 0) or 0),
            session_count=int(summary.get("session_count", 0) or 0),
            session_capacity=int(summary.get("session_capacity", 0) or 0) or 1,
            mbuf_usage=float(summary.get("mbuf_usage", 0) or 0),
            ha_status=summary.get("ha_status", "standalone"),
        )

    async def _status_from_parts(self) -> dict:
        """真实设备（如 AF 8.0.45）无聚合状态端点时逐项拉取，字段形态防御性解析。"""
        out: dict = {}

        def _num(payload, *keys) -> float:
            if not isinstance(payload, dict):
                return 0.0
            for k in keys:
                v = payload.get(k)
                if v not in (None, ""):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        continue
            return 0.0

        results = await asyncio.gather(
            self._request("GET", f"/api/v1/namespaces/{self.namespace}/cpuusage"),
            self._request("GET", f"/api/v1/namespaces/{self.namespace}/memoryusage"),
            self._request("GET", f"/api/v1/namespaces/{self.namespace}/diskusage"),
            self._request("GET", f"/api/v1/namespaces/{self.namespace}/uptimes"),
            return_exceptions=True)
        cpu = results[0] if not isinstance(results[0], BaseException) else {}
        mem = results[1] if not isinstance(results[1], BaseException) else {}
        disk = results[2] if not isinstance(results[2], BaseException) else {}
        ups = results[3] if not isinstance(results[3], BaseException) else {}
        out["cpu_usage"] = _num(cpu if isinstance(cpu, dict) else {}, "cpuCurrent", "cpuAverage", "usage", "value")
        out["memory_usage"] = _num(mem if isinstance(mem, dict) else {}, "memoryUsage", "memory_usage", "usage")
        out["disk_usage"] = _num(disk if isinstance(disk, dict) else {}, "diskUsage", "disk_usage", "usage")
        if isinstance(ups, dict):
            out["uptime"] = ups.get("upTimes") or ups.get("uptime", "")
        self.capability_gaps["status_summary"] = "该版本无聚合状态端点，已降级为逐项查询"
        return out

    @staticmethod
    def _rows(data) -> list[dict]:
        """兼容列表响应两种形态：新版 {items: [...]} 与模拟器 {list: [...]}。"""
        if isinstance(data, dict):
            return data.get("items") or data.get("list") or []
        return data or []

    @staticmethod
    def _first(seq, default="any"):
        return str(seq[0]) if seq else default

    @staticmethod
    def _join(seq, default="any"):
        return ",".join(str(x) for x in seq) if seq else default

    # ---------- 网络：接口 ----------
    async def get_interfaces(self) -> list[InterfaceInfo]:
        data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/interfaces",
                                   {"_start": 0, "_length": MAX_PAGE_LENGTH})
        out = []
        for r in self._rows(data):
            if "uuid" in r:   # 真实设备形态（AF 8.0.4x/8.0.10x）
                ip, bits, extra_ips = "", "", ""
                # SWITCH 模式接口可能没有 ipv4 字段，需要安全访问
                ipv4 = r.get("ipv4") or {}
                static_ips = ipv4.get("staticIp") or []
                if static_ips:
                    addr = static_ips[0].get("ipaddress") or {}
                    ip = str(addr.get("start", ""))
                    end = str(addr.get("end", ""))
                    bits = str(addr.get("bits", ""))
                    # 如果 start 和 end 不同，表示连续地址段，显示为 range
                    if end and end != ip:
                        ip = f"{ip} - {end}"
                    # 收集附加IP地址（从第二个 staticIp 开始）
                    extra_parts = []
                    for s in static_ips[1:]:
                        a = s.get("ipaddress") or {}
                        sip = str(a.get("start", ""))
                        send = str(a.get("end", ""))
                        sbits = str(a.get("bits", ""))
                        if sip:
                            if send and send != sip:
                                extra_parts.append(f"{sip} - {send}")
                            else:
                                extra_parts.append(f"{sip}/{sbits}" if sbits else sip)
                    extra_ips = ", ".join(extra_parts)
                # 尝试从多个位置提取区域（真实设备 zone 字段位置不一）
                zone = ""
                for zone_key in ("zone", "zoneName", "securityZone"):
                    zone_raw = r.get(zone_key)
                    if zone_raw is not None:
                        if isinstance(zone_raw, dict):
                            zone = str(zone_raw.get("name", ""))
                        elif isinstance(zone_raw, str):
                            zone = zone_raw
                        if zone:
                            break
                # 如果顶层没找到，尝试从 physicalif/subif 中提取
                if not zone:
                    phys = r.get("physicalif") or {}
                    for zk in ("zone", "zoneName", "securityZone"):
                        zr = phys.get(zk)
                        if zr is not None:
                            zone = str(zr.get("name", zr)) if isinstance(zr, dict) else str(zr)
                            if zone:
                                break
                if not zone:
                    sub = r.get("subif") or {}
                    for zk in ("zone", "zoneName", "securityZone"):
                        zr = sub.get(zk)
                        if zr is not None:
                            zone = str(zr.get("name", zr)) if isinstance(zr, dict) else str(zr)
                            if zone:
                                break
                # 状态判断：优先级 operStatus（含物理连路）> physicalif 链路状态 > adminStatus > shutdown
                status = "down"
                oper_st = r.get("operStatus")
                if oper_st is not None:
                    # operStatus 存在即以它为准（支持字符串 up/down 和数字 1/0）
                    if isinstance(oper_st, str) and oper_st.lower() == "up":
                        status = "up"
                    elif isinstance(oper_st, (int, float)) and oper_st == 1:
                        status = "up"
                    else:
                        status = "down"
                else:
                    # 检查 physicalif 子对象中的链路状态
                    phys = r.get("physicalif") or {}
                    phys_link = None
                    for lk in ("operStatus", "linkStatus", "link", "phyStatus", "status"):
                        lv = phys.get(lk)
                        if lv is not None:
                            phys_link = lv
                            break
                    if phys_link is not None:
                        if isinstance(phys_link, str) and phys_link.lower() == "up":
                            status = "up"
                        elif isinstance(phys_link, (int, float)) and phys_link == 1:
                            status = "up"
                        else:
                            status = "down"
                    elif "adminStatus" in r:
                        admin_st = r.get("adminStatus", "down")
                        if isinstance(admin_st, str):
                            status = "up" if admin_st.lower() == "up" else "down"
                    elif "shutdown" in r:
                        # shutdown=false 仅表示管理启用，不等同于物理链路 UP
                        # 配置接口无实时链路字段：shutdown=true 判禁用；false 以配置状态呈现
                        if r.get("shutdown") is True:
                            status = "down"
                        else:
                            status = "enabled"
                out.append(InterfaceInfo(
                    name=r.get("name", ""), zone=zone, ip=ip, netmask=bits,
                    extra_ips=extra_ips, status=status,
                    speed=str((r.get("physicalif") or {}).get("speedDuplex", {}).get("speed", "") or ""),
                    mac=r.get("mac", ""), rx_kbps=0, tx_kbps=0,
                    comment=r.get("description", "")))
            else:
                # 兼容真实设备无 uuid 字段的情况（如 AF 8.0.45），从多个位置提取区域
                zone = ""
                for zone_key in ("zone", "zoneName", "securityZone"):
                    zr = r.get(zone_key)
                    if zr is not None:
                        zone = str(zr.get("name", zr)) if isinstance(zr, dict) else str(zr)
                        if zone:
                            break
                # 如果顶层没找到，尝试从 physicalif/subif 中提取
                if not zone:
                    phys = r.get("physicalif") or {}
                    for zk in ("zone", "zoneName", "securityZone"):
                        zr = phys.get(zk)
                        if zr is not None:
                            zone = str(zr.get("name", zr)) if isinstance(zr, dict) else str(zr)
                            if zone:
                                break
                if not zone:
                    sub = r.get("subif") or {}
                    for zk in ("zone", "zoneName", "securityZone"):
                        zr = sub.get(zk)
                        if zr is not None:
                            zone = str(zr.get("name", zr)) if isinstance(zr, dict) else str(zr)
                            if zone:
                                break
                # 状态：优先级 operStatus > physicalif 链路状态 > adminStatus > shutdown
                st = "down"
                oper_st = r.get("operStatus")
                if oper_st is not None:
                    if isinstance(oper_st, str) and oper_st.lower() == "up":
                        st = "up"
                    elif isinstance(oper_st, (int, float)) and oper_st == 1:
                        st = "up"
                    else:
                        st = "down"
                else:
                    phys = r.get("physicalif") or {}
                    phys_link = None
                    for lk in ("operStatus", "linkStatus", "link", "phyStatus", "status"):
                        lv = phys.get(lk)
                        if lv is not None:
                            phys_link = lv
                            break
                    if phys_link is not None:
                        if isinstance(phys_link, str) and phys_link.lower() == "up":
                            st = "up"
                        elif isinstance(phys_link, (int, float)) and phys_link == 1:
                            st = "up"
                        else:
                            st = "down"
                    elif "shutdown" in r:
                        if r.get("shutdown") is True:
                            st = "down"
                    else:
                        for st_key in ("adminStatus", "ifOperStatus", "ifAdminStatus", "status"):
                            st_v = r.get(st_key)
                            if isinstance(st_v, str) and st_v.lower() == "up":
                                st = "up"
                                break
                            elif isinstance(st_v, str) and st_v.lower() == "down":
                                st = "down"
                                break
                out.append(InterfaceInfo(
                    name=r.get("name", ""), zone=zone, ip=r.get("ip", ""),
                    netmask=r.get("netmask", ""), status=st,
                    speed=r.get("speed", ""), mac=r.get("mac", ""),
                    rx_kbps=float(r.get("rx_kbps", 0) or 0), tx_kbps=float(r.get("tx_kbps", 0) or 0),
                    comment=r.get("comment", "")))
        # 如果接口有缺失 zone 信息，尝试从 zones 端点获取区域映射关系补充
        if any(not i.zone for i in out):
            try:
                zones_data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/zones",
                                                 {"_start": 0, "_length": MAX_PAGE_LENGTH})
                zone_list = self._rows(zones_data)
                if zone_list:
                    zone_map: dict[str, str] = {}
                    for z in zone_list:
                        zname = z.get("name", "")
                        for if_name in (z.get("interfaces") or []):
                            zone_map[if_name] = zname
                    for iface in out:
                        if not iface.zone and iface.name in zone_map:
                            iface.zone = zone_map[iface.name]
            except DeviceError:
                pass
        return out

    # ---------- NAT ----------
    async def get_nat_rules(self) -> list[NatRule]:
        data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/nats",
                                   {"_start": 0, "_length": MAX_PAGE_LENGTH})
        out = []
        for r in self._rows(data):
            if "uuid" in r:   # 真实设备形态
                ntype = str(r.get("natType", "SNAT")).upper()
                # BNAT（双向NAT）使用 bnat 字段，包含 transferDst（DNAT方向）和 transferSrc（SNAT方向）
                if ntype == "BNAT":
                    body = r.get("bnat") or {}
                    transfer_dst = body.get("transferDst") or {}
                    transfer_src = body.get("transferSrc") or {}
                    dst_ip = self._first((body.get("dstIpobj") or {}).get("specifyIp"), "")
                    translated = transfer_dst.get("specifyIp", "")
                    if isinstance(translated, list):
                        translated = ",".join(str(x) for x in translated)
                    port = ""
                    tports = transfer_dst.get("transferPort") or []
                    if tports:
                        port = ",".join(str(p) for p in tports if isinstance(p, (str, int)))
                    out.append(NatRule(
                        id=r.get("uuid", ""), name=r.get("name", ""), enabled=bool(r.get("enable", True)),
                        type=ntype,
                        src_zone=self._first(body.get("srcZones"), "any"),
                        dst_zone=self._first(body.get("dstZones"), "any") or "any",
                        src_addr=self._join(body.get("srcIpGroups")),
                        dst_addr=dst_ip or "any",
                        service=self._join(body.get("natService") or body.get("services")),
                        translated_addr=str(translated or ""),
                        translated_port=str(port or ""),
                        hit_count=int(r.get("natHit", 0) or 0), log=bool(r.get("log", False)),
                        comment=r.get("description", "")))
                else:
                    body = r.get("dnat") or r.get("snat") or {}
                    transfer = body.get("transfer") or {}
                    translated = transfer.get("specifyIp", "")
                    if isinstance(translated, list):
                        translated = ",".join(str(x) for x in translated)
                    port = transfer.get("specifyPort") or transfer.get("port") or ""
                    dst_obj = body.get("dstIpobj") or {}
                    dst_ip = self._first(dst_obj.get("specifyIp"), "") if ntype == "DNAT" else ""
                    out.append(NatRule(
                        id=r.get("uuid", ""), name=r.get("name", ""), enabled=bool(r.get("enable", True)),
                        type=ntype,
                        src_zone=self._first(body.get("srcZones"), "any"),
                        dst_zone="untrust" if ntype == "DNAT" else self._first(body.get("dstZones"), "any"),
                        src_addr=self._join(body.get("srcIpGroups")),
                        dst_addr=f"{dst_ip}:{port}" if port and dst_ip else (dst_ip or "any"),
                        service=self._join(body.get("natService") or body.get("services")),
                        translated_addr=str(translated or ""),
                        translated_port=str(port or ""),
                        hit_count=int(r.get("natHit", 0) or 0), log=bool(r.get("log", False)),
                        comment=r.get("description", "")))
            else:
                out.append(NatRule(**{k: r.get(k, d) for k, d in
                        (("id", ""), ("name", ""), ("enabled", True), ("type", "SNAT"), ("src_zone", "any"),
                         ("dst_zone", "any"), ("src_addr", "any"), ("dst_addr", "any"), ("service", "any"),
                         ("translated_addr", ""), ("translated_port", ""), ("hit_count", 0),
                         ("log", True), ("comment", ""))}))
        return out

    # ---------- 访问控制 ----------
    async def get_acl_rules(self) -> list[AclRule]:
        data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/appcontrols/policys",
                                   {"_start": 0, "_length": MAX_PAGE_LENGTH})
        out = []
        for r in self._rows(data):
            if "uuid" in r:   # 真实设备形态
                src = r.get("src") or {}
                dst = r.get("dst") or {}
                src_addrs = src.get("srcAddrs") or {}
                dst_addrs = dst.get("dstAddrs") or {}
                action = r.get("action")
                # 实测 AF 8.0.45：0=拒绝（默认策略 Default Policy action=0 兜底拒绝），
                # 1=允许（名为 allow 的放行策略 action=1 持续命中）
                action_str = {0: "deny", 1: "allow"}.get(action, str(action))
                # lastHitTime 为 1970 表示从未命中
                never_hit = str(r.get("lastHitTime", "")).startswith("1970")
                out.append(AclRule(
                    id=r.get("uuid", ""), name=r.get("name", ""), enabled=bool(r.get("enable", True)),
                    src_zone=self._join(src.get("srcZones")),
                    dst_zone=self._join(dst.get("dstZones")),
                    src_addr=self._join(src_addrs.get("srcIpGroups")),
                    dst_addr=self._join(dst_addrs.get("dstIpGroups") or dst_addrs.get("srcIpGroups")),
                    service=self._join(dst.get("services")),
                    app=self._join(dst.get("applications")),
                    action=action_str,
                    hit_count=0 if never_hit else -1,
                    log=bool((r.get("advanceOption") or {}).get("logEnable", False)),
                    comment=r.get("description", "")))
            else:
                out.append(AclRule(**{k: r.get(k, d) for k, d in
                        (("id", ""), ("name", ""), ("enabled", True), ("src_zone", "any"), ("dst_zone", "any"),
                         ("src_addr", "any"), ("dst_addr", "any"), ("service", "any"), ("app", "any"),
                         ("action", "deny"), ("hit_count", 0), ("log", False), ("comment", ""))}))
        return out

    async def get_user_bindings(self, keyword: str = "") -> list[UserBinding]:
        data = await self._optional_list(f"/api/v1/namespaces/{self.namespace}/userbindings")()
        return [UserBinding(
            id=r.get("id", "") or r.get("uuid", ""), user=r.get("user", ""), ip=r.get("ip", ""), mac=r.get("mac", ""),
            binding_type=r.get("binding_type", "static"), enabled=bool(r.get("enabled", True)),
            comment=r.get("comment", "")) for r in self._rows(data)]

    async def get_network_objects(self) -> list[NetworkObject]:
        data = await self._optional_list(f"/api/v1/namespaces/{self.namespace}/ipgroups")()
        out = []
        for r in self._rows(data):
            if "uuid" in r:   # 真实设备形态：ipRanges [{start,end}] 区间列表
                members = ",".join(
                    f"{rg.get('start')}-{rg.get('end')}" if rg.get("end") not in (None, "", rg.get("start"))
                    else str(rg.get("start"))
                    for rg in (r.get("ipRanges") or []))
                out.append(NetworkObject(
                    id=r.get("uuid", ""), name=r.get("name", ""), type="ipgroup",
                    members=members or "any", comment=r.get("description", "")))
            else:
                out.append(NetworkObject(
                    id=r.get("id", ""), name=r.get("name", ""), type=r.get("type", "ipgroup"),
                    members=r.get("members", ""), comment=r.get("comment", "")))
        return out

    async def get_services(self) -> list[ServiceConfig]:
        """自定义服务：仅保留 servType=USRDEF_SERV（排除设备预定义服务），并映射端口结构。"""
        data = await self._optional_list(f"/api/v1/namespaces/{self.namespace}/services")()
        out = []
        for r in self._rows(data):
            if "uuid" in r:   # 真实设备形态
                if r.get("servType") != "USRDEF_SERV":
                    continue
                protocol, ports = self._ports_from_entries(r)
                out.append(ServiceConfig(
                    id=r.get("uuid", ""), name=r.get("name", ""), protocol=protocol,
                    ports=ports, comment=r.get("description", "")))
            elif r.get("servType", "USRDEF_SERV") == "USRDEF_SERV":
                out.append(ServiceConfig(
                    id=r.get("id", ""), name=r.get("name", ""), protocol=r.get("protocol", "TCP"),
                    ports=r.get("ports", ""), comment=r.get("comment", "")))
        return out

    @staticmethod
    def _ports_from_entries(r: dict) -> tuple[str, str]:
        """tcpEntrys/udpEntrys/icmpEntrys → (协议, 端口串，如 '135,445' / '8000-8010')。"""
        def fmt(entry_list):
            parts = []
            for e in entry_list or []:
                for p in e.get("dstPorts") or []:
                    s, e_ = p.get("start"), p.get("end")
                    if e_ in (None, "", s):
                        parts.append(str(s))
                    else:
                        parts.append(f"{s}-{e_}")
            return ",".join(dict.fromkeys(parts))

        protocols, tcp_ports, udp_ports = [], "", ""
        if r.get("tcpEntrys"):
            protocols.append("TCP")
            tcp_ports = fmt(r["tcpEntrys"])
        if r.get("udpEntrys"):
            protocols.append("UDP")
            udp_ports = fmt(r["udpEntrys"])
        if r.get("icmpEntrys"):
            protocols.append("ICMP")
        ports = tcp_ports if tcp_ports == udp_ports else ",".join(p for p in (tcp_ports, udp_ports) if p)
        return ("/".join(protocols) or "TCP"), ports

    async def get_interface_status(self, ifaces: list[dict], concurrency: int = 5) -> dict[str, dict]:
        """实时接口状态（状态中心 8.1.1.10）：连接状态 + 收发速率。

        按网口名逐个调 /interfacestatus/{name}（该端点必须带接口名路径段），
        并发受信号量限制；单口失败静默跳过，调用方保留配置层状态。
        返回 {接口名: {connect: bool, rx_kbps: float, tx_kbps: float}}。
        """
        out: dict[str, dict] = {}
        sem = asyncio.Semaphore(concurrency)

        async def _one(name: str):
            async with sem:
                try:
                    data = await self._request(
                        "GET", f"/api/v1/namespaces/{self.namespace}/interfacestatus/{name}", {})
                    for r in self._rows(data):
                        if r.get("interfaceName") != name:
                            continue
                        info = r.get("information") or {}
                        speed = info.get("speed") or {}
                        return name, {
                            "connect": bool(info.get("connectStatus")),
                            "rx_kbps": float(speed.get("recv") or 0),
                            "tx_kbps": float(speed.get("send") or 0),
                        }
                except Exception:   # noqa: BLE001 —— 单口失败不影响其它口
                    return None

        names = [str(i.get("name")) for i in ifaces if i.get("name")]
        results = await asyncio.gather(*[_one(n) for n in names])
        for r in results:
            if r:
                out[r[0]] = r[1]
        return out

    async def get_static_routes(self) -> list[StaticRoute]:
        data = await self._optional_list(f"/api/v1/namespaces/{self.namespace}/staticroutes/ipv4")()
        out = []
        for r in self._rows(data):
            if "uuid" in r:   # 真实设备形态
                out.append(StaticRoute(
                    id=r.get("uuid", ""), name=r.get("description", ""), dst=r.get("prefix", ""),
                    next_hop=r.get("gateway", ""), interface=r.get("ifname", ""),
                    distance=int(r.get("distance", 10) or 10),
                    enabled=bool(r.get("enable", True)), comment=""))
            else:
                out.append(StaticRoute(
                    id=r.get("id", ""), name=r.get("name", ""), dst=r.get("dst", ""), next_hop=r.get("next_hop", ""),
                    interface=r.get("interface", ""), distance=int(r.get("distance", 10)),
                    enabled=bool(r.get("enabled", True)), comment=r.get("comment", "")))
        return out

    # ---------- 变更 ----------
    _PATHS = {
        "nat": "/nats",
        "acl": "/appcontrols/policys",
        "binding": "/userbindings",
        "route": "/staticroutes/ipv4",
        "object": "/ipgroups",
        "service": "/services",
        "whiteblacklist": "/whiteblacklist",
    }

    async def apply_change(self, change: ChangeOp) -> dict:
        path = self._PATHS.get(change.resource)
        if not path:
            raise DeviceError(f"不支持的资源类型：{change.resource}")
        base = f"/api/v1/namespaces/{self.namespace}{path}"
        data = self._translate_write(change) if self.real_shape else change.data
        target = change.target_id
        if change.op in ("delete", "update") and self.real_shape and change.resource in ("object", "service", "nat", "acl", "binding"):
            # 真实设备删除/修改按资源名称定位（uuid 仅用于列表标识）
            target = await self._resolve_name(change.resource, change.target_id) or change.target_id
        if change.op == "create":
            resp = await self._request("POST", base, data)
            return {"ok": True, "message": f"已创建 {change.resource} 记录", "data": resp}
        if change.op == "update":
            resp = await self._request("PATCH", f"{base}/{target}", data)
            return {"ok": True, "message": f"已更新 {change.resource}#{target}", "data": resp}
        if change.op == "delete":
            await self._request("DELETE", f"{base}/{target}")
            return {"ok": True, "message": f"已删除 {change.resource}#{target}", "data": None}
        raise DeviceError(f"不支持的操作：{change.op}")

    async def _resolve_name(self, resource: str, target_id: str) -> str:
        """uuid → 资源名称（真实设备删除/修改按 name 定位）。"""
        path = self._PATHS.get(resource, "")
        try:
            data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}{path}",
                                       {"_start": 0, "_length": MAX_PAGE_LENGTH})
            for r in self._rows(data):
                if r.get("uuid") == target_id or r.get("id") == target_id:
                    return r.get("name", "")
        except DeviceError:
            pass
        return ""

    # ---------- 真实设备写格式翻译（Agent 扁平字段 → 设备原生结构） ----------
    @staticmethod
    def _ip_ranges(members: str) -> list[dict]:
        """'10.1.1.0/24,192.168.1.5,10.0.0.1-10.0.0.10' → ipRanges [{start,end}]。"""
        import ipaddress
        ranges = []
        for part in (members or "").replace("，", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                if "-" in part and not part.count(":"):
                    start, end = [p.strip() for p in part.split("-", 1)]
                    ipaddress.ip_address(start)
                    ipaddress.ip_address(end)
                    ranges.append({"start": start, "end": end})
                else:
                    net = ipaddress.ip_network(part, strict=False)
                    ranges.append({"start": str(net.network_address),
                                   "end": str(net.broadcast_address)})
            except ValueError:
                continue
        return ranges

    @classmethod
    def _service_payload(cls, data: dict) -> dict:
        """{name, protocol, ports, comment} → 设备原生 servType/tcpEntrys/udpEntrys。"""
        import re as _re
        protocol = str(data.get("protocol", "TCP")).upper()
        payload = {"name": data.get("name", ""), "servType": "USRDEF_SERV",
                   "description": data.get("comment", data.get("description", ""))}
        dst_ports = []
        for part in _re.split(r"[，,]", str(data.get("ports", "") or "")):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                s, e = part.split("-", 1)
                dst_ports.append({"start": int(s), "end": int(e)})
            else:
                dst_ports.append({"start": int(part), "end": int(part)})
        entry = {"srcPorts": [{"start": 0, "end": 65535}],
                 "dstPorts": dst_ports or [{"start": 0, "end": 65535}]}
        if "UDP" in protocol and "TCP" not in protocol:
            payload["udpEntrys"] = [entry]
        else:
            payload["tcpEntrys"] = [entry]
            if "UDP" in protocol:
                payload["udpEntrys"] = [entry]
        return payload

    def _translate_write(self, change: ChangeOp) -> dict:
        data = dict(change.data or {})
        if change.resource == "object" and ("members" in data or "comment" in data):
            payload = {"name": data.get("name", ""), "businessType": "IP", "addressType": "IPV4",
                       "description": data.get("comment", data.get("description", "")),
                       "ipRanges": self._ip_ranges(str(data.get("members", "")))}
            data = {**{k: v for k, v in data.items() if k in ("name",)}, **payload}
        elif change.resource == "service" and ("ports" in data or "protocol" in data):
            data = self._service_payload(data)
        elif change.resource == "whiteblacklist":
            # API文档: url=IP/域名/URL, type=BLACK/WHITE, enable=true/false, description=描述
            list_type = data.get("type", "BLACK")
            if isinstance(list_type, str):
                list_type = "BLACK" if list_type.upper() in ("BLACK", "黑名单") else "WHITE"
            payload = {
                "url": data.get("url", ""),
                "type": list_type,
                "enable": data.get("enable", True),
                "description": data.get("description", ""),
            }
            data = payload
        return data

    # ---------- 安全区域（Zone） ----------
    async def get_zones(self) -> list[dict]:
        """获取安全区域（Zone）定义。优先从设备 /zones 端点查询，降级时从接口信息提取。"""
        try:
            data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/zones",
                                       {"_start": 0, "_length": MAX_PAGE_LENGTH})
            zones = self._rows(data)
            if zones:
                for z in zones:
                    z["name"] = z.get("name", "")
                    # 将 forwardType 映射为前端展示的 type 字段
                    if "forwardType" in z and "type" not in z:
                        z["type"] = z["forwardType"]
                    # 确保 interfaces 字段存在
                    z.setdefault("interfaces", [])
                    # 提供一个空 members 字段供前端展示
                    z.setdefault("members", [])
                return zones
        except DeviceError:
            pass
        # 降级：从接口信息中提取区域
        self.capability_gaps["zones"] = "设备无独立安全区域端点，已从接口信息提取"
        ifaces = await self.get_interfaces()
        zone_map: dict[str, list[str]] = {}
        for iface in ifaces:
            if iface.zone:
                zone_map.setdefault(iface.zone, []).append(iface.name)
        return [{"name": z, "type": "inferred", "interfaces": ifs, "members": []}
                for z, ifs in zone_map.items()]

    # ---------- 配置文件（模拟控制台私有端点，真实设备若端点不可用会抛错并降级） ----------
    async def backup_config_file(self) -> tuple[bytes, str]:
        resp = await self._client.get(
            f"/api/v1/namespaces/{self.namespace}/configfile/download",
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
            f"/api/v1/namespaces/{self.namespace}/configfile/upload",
            content=data, headers={"Cookie": f"token={self._token}",
                                   "Content-Type": "application/octet-stream"})
        if resp.status_code != 200:
            raise DeviceError(f"配置文件上传失败 HTTP {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if payload.get("code") != 0:
            raise DeviceError(payload.get("message", "配置恢复被设备拒绝"))
        return payload.get("data") or {}
