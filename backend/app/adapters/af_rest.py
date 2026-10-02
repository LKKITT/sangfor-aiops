"""深信服 AF REST API 适配器。

按官方 AF 8.0.x REST API 实现（support.sangfor.com.cn《API 帮助文档》，AF8.0.107 共 1235 个操作）：
- POST /api/v1/namespaces/{ns}/login 用 name/password 换取 token，token 以 Cookie 携带；
- 业务失败码 1003/1012（会话失效）时自动重登一次；
- 列表接口支持 _start/_length 分页；
- 同一客户端可指向真实设备或内置模拟器（端点同构）。
- 设备 HTTPS 普遍使用旧密码套件（如 TLS1.2 + AES256-SHA）：关闭证书校验并放宽 SSL 安全等级。
"""
import asyncio
import re
import ssl
import time
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
# 版本探测缓存 TTL：版本号仅在设备升级后变化。get_status 被状态/体检/知识库/升级
# 等高频复用，而慢设备探测需串行尝试多个端点甚至抓取 Web 页（秒级~数十秒），必须缓存。
VERSION_CACHE_TTL = 600.0        # 探测成功
VERSION_CACHE_TTL_MISS = 60.0    # 全部探测失败（设备暂时不可达），短缓存后快速重试


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
        # 版本探测缓存（get_status 高频复用，避免每次串行试探多个端点）
        self._version_cache: dict | None = None
        self._version_cached_at: float = 0.0
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
        """安全获取版本信息（带短 TTL 缓存，见 VERSION_CACHE_TTL）。

        AF 8.0.45/8.0.48 等设备 systemversion 返回 code=1007，需依次尝试多种方案，
        结果缓存后高频复用方（状态/体检/知识库/升级建议）不再重复支付探测耗时。
        """
        now = time.monotonic()
        if self._version_cache is not None:
            ttl = VERSION_CACHE_TTL if self._version_cache else VERSION_CACHE_TTL_MISS
            if now - self._version_cached_at < ttl:
                return self._version_cache
        version = await self._probe_version()
        self._version_cache = version
        self._version_cached_at = now
        return version

    async def _probe_version(self) -> dict:
        """实际探测：依次尝试版本端点 → 登录响应 → 设备 Web 页面。"""
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
            session_capacity=int(summary.get("session_capacity", 0) or 0),
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
            self._request("GET", f"/api/v1/namespaces/{self.namespace}/topsessionnumbers",
                          {"topFilter": "TOTAL", "_start": 0, "_length": MAX_PAGE_LENGTH}),
            return_exceptions=True)
        cpu = results[0] if not isinstance(results[0], BaseException) else {}
        mem = results[1] if not isinstance(results[1], BaseException) else {}
        disk = results[2] if not isinstance(results[2], BaseException) else {}
        ups = results[3] if not isinstance(results[3], BaseException) else {}
        tops = results[4] if not isinstance(results[4], BaseException) else {}
        out["cpu_usage"] = _num(cpu if isinstance(cpu, dict) else {}, "cpuCurrent", "cpuAverage", "usage", "value")
        out["memory_usage"] = _num(mem if isinstance(mem, dict) else {}, "memoryUsage", "memory_usage", "usage")
        out["disk_usage"] = _num(disk if isinstance(disk, dict) else {}, "diskUsage", "disk_usage", "usage")
        if isinstance(ups, dict):
            out["uptime"] = ups.get("upTimes") or ups.get("uptime", "")
        # 会话总数：状态中心 8.2.2.1 会话数量排行（topFilter=TOTAL）按内网 IP 求和；
        # 设备 API 未提供全局会话容量上限，capacity 保持未知（0）
        total = self._sum_session_totals(tops if isinstance(tops, dict) else {})
        if total:
            out["session_count"] = total
            self.capability_gaps.setdefault(
                "session_capacity", "设备 API 未提供会话容量上限，容量未知")
        if isinstance(ups, dict):
            out["uptime"] = ups.get("upTimes") or ups.get("uptime", "")
        self.capability_gaps["status_summary"] = "该版本无聚合状态端点，已降级为逐项查询"
        return out

    @staticmethod
    def _sum_session_totals(payload: dict) -> int:
        """会话数量排行响应（topsessionnumbers）→ 各内网 IP 总会话数求和；取不到返回 0。"""
        total = 0
        for it in (payload.get("items") or []):
            sn = it.get("sessionNumber") if isinstance(it, dict) else None
            try:
                total += int((sn or {}).get("total", 0) or 0)
            except (TypeError, ValueError):
                continue
        return total

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
                    translated = self._transfer_addr_str(transfer_dst)
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
                    translated = self._transfer_addr_str(transfer)
                    port = self._transfer_port_str(transfer)
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
    @classmethod
    def _acl_flat(cls, r: dict) -> dict:
        """原生应用控制策略 → Agent 扁平字段（与 API 文档结构一致，供展示与变更比对）。"""
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
        return {
            "id": r.get("uuid", ""), "name": r.get("name", ""),
            "enabled": bool(r.get("enable", True)),
            "src_zone": cls._join(src.get("srcZones"), "any"),
            "dst_zone": cls._join(dst.get("dstZones"), "any"),
            "src_addr": cls._join(src_addrs.get("srcIpGroups"), "全部"),
            "dst_addr": cls._join(dst_addrs.get("dstIpGroups") or dst_addrs.get("srcIpGroups"),
                                  "全部"),
            "service": cls._join(dst.get("services"), "any"),
            "app": cls._join(dst.get("applications"), "全部"),
            "action": action_str,
            "hit_count": 0 if never_hit else -1,
            "log": bool((r.get("advanceOption") or {}).get("logEnable", False)),
            "comment": r.get("description", ""),
        }

    async def get_acl_rules(self) -> list[AclRule]:
        data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/appcontrols/policys",
                                   {"_start": 0, "_length": MAX_PAGE_LENGTH})
        out = []
        for r in self._rows(data):
            if "uuid" in r:   # 真实设备形态
                out.append(AclRule(**self._acl_flat(r)))
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

    async def get_predefined_services(self) -> list[ServiceConfig]:
        """设备预定义服务（servType=PREDEF_SERV：ftp/https/any 等内置服务）。"""
        data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/services",
                                   {"servType": "PREDEF_SERV",
                                    "_start": 0, "_length": MAX_PAGE_LENGTH})
        out = []
        for r in self._rows(data):
            if "uuid" not in r:
                continue
            protocol, ports = self._ports_from_entries(r)
            out.append(ServiceConfig(
                id=r.get("uuid", ""), name=r.get("name", ""), protocol=protocol,
                ports=ports, comment=r.get("description", "")))
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
        if self.real_shape and change.resource == "nat" and change.op == "update":
            # NAT 要求原生嵌套结构（DNAT 目的地址在 dnat.dstIpobj.specifyIp 等），
            # 扁平字段会被设备静默忽略——返回成功但配置不变
            raw = await self._raw_native_rule(self._PATHS["nat"], change.target_id)
            data = self._nat_native_payload(raw, change.data)
        if self.real_shape and change.resource == "nat" and change.op == "create":
            data = self._nat_create_payload(change.data)
        if self.real_shape and change.resource == "acl":
            if change.op in ("update", "create"):
                # 引用的 IP组/服务 不存在时自动创建（裸 IP/网段 → 同名网络对象；
                # 端口形态 → 同名自定义服务），再执行变更
                await self._ensure_acl_addr_groups(change.data)
                await self._ensure_acl_services(change.data)
            # 应用控制策略要求原生结构（action 为 0/1 整数、嵌套 src/dst 对象等），
            # 扁平字段直接下发会被设备以"[策略动作]：参数类型不匹配"拒绝
            if change.op == "update":
                raw = await self._acl_raw_rule(change.target_id)
                data = self._acl_native_payload(raw, change.data)
            elif change.op == "create":
                data = self._acl_create_payload(change.data)
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

    # ---------- 应用控制策略（ACL）真实设备原生格式 ----------
    @staticmethod
    def _ip_like(part: str) -> bool:
        """是否为可自动创建为 IP组 的形态：IP / CIDR / IP-IP 范围。"""
        import ipaddress
        part = part.strip()
        try:
            ipaddress.ip_network(part, strict=False)
            return True
        except ValueError:
            pass
        if "-" in part:
            try:
                start, end = part.split("-", 1)
                ipaddress.ip_address(start.strip())
                ipaddress.ip_address(end.strip())
                return True
            except ValueError:
                pass
        return False

    async def _ensure_acl_addr_groups(self, data: dict) -> None:
        """确保 ACL 引用的地址（IP组）存在：裸 IP/网段自动创建同名网络对象后再引用。

        设备要求 srcIpGroups/dstIpGroups 引用已存在的网络对象名称，直接给
        192.168.1.10 这类裸 IP 会报"网络对象[x] 不存在"。这里对可解析为
        IP/网段/范围的缺失项自动创建同名 IP组（CIDR 的"/"替换为"_"）并同步改写
        引用值；名称形态（如"总部IP组"、内置"全部"）不存在的交给设备校验，不代建。
        """
        for key in ("src_addr", "dst_addr"):
            value = str(data.get(key) or "").strip()
            # 地址字段约定：任意地址对应内置网络对象「全部」（官方文档样例即引用"全部"），
            # 区域字段的 "any" 惯例不能用于地址引用，设备会报"网络对象[any] 不存在"
            if value.lower() in ("any", "any4", "任意", "所有"):
                data[key] = value = "全部"
            if not value:
                continue
            parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
            existing = {o.name for o in await self.get_network_objects()}
            missing = [p for p in parts if p not in existing and self._ip_like(p)]
            for part in missing:
                name = part.replace("/", "_")
                await self.apply_change(ChangeOp(
                    op="create", resource="object", target_id="",
                    data={"name": name, "members": part,
                          "comment": "访问控制策略引用，由 Agent 自动创建"}))
                if name != part:
                    parts[parts.index(part)] = name
            if any(p not in existing for p in parts):
                data[key] = ",".join(parts)

    @staticmethod
    def _parse_port_spec(part: str):
        """解析端口形态的服务引用：'TCP5211'/'tcp/5211'/'5211'/'UDP 53'/'5211-5220'
        → (协议大写, 端口串)；非端口形态返回 None。无协议前缀时默认 TCP。"""
        import re as _re
        m = _re.fullmatch(r"(?:(tcp|udp)[\s/:_-]*)?(\d{1,5})(?:\s*-\s*(\d{1,5}))?",
                          str(part).strip(), _re.IGNORECASE)
        if not m:
            return None
        proto = (m.group(1) or "tcp").upper()
        start, end = m.group(2), m.group(3) or m.group(2)
        return proto, (start if start == end else f"{start}-{end}")

    async def _ensure_acl_services(self, data: dict) -> None:
        """确保 ACL 引用的服务存在：自定义 → 预定义 → 端口形态自动建自定义服务。

        设备要求 services 引用服务名称。解析顺序：自定义服务（USRDEF_SERV）→
        预定义服务（PREDEF_SERV，如 ftp/any）→ 端口形态（如 TCP5211/5211-5220）
        自动创建同名自定义服务（同协议同端口的服务已存在则直接复用）；均未命中
        且无端口信息时明确报错（无法凭空推断端口），不盲建空服务。
        """
        value = str(data.get("service") or "").strip()
        if not value:
            return
        parts = [p.strip() for p in value.replace("，", ",").split(",") if p.strip()]
        custom = {s.name: s for s in await self.get_services()}
        try:
            predefined = {s.name for s in await self.get_predefined_services()}
        except Exception:   # noqa: BLE001 —— 预定义列表不可用时保留原值交由设备校验
            predefined = None
        changed = False
        for i, part in enumerate(parts):
            if part in custom or (predefined is not None and part in predefined):
                continue
            spec = AfRestClient._parse_port_spec(part)
            if not spec:
                if predefined is not None:
                    raise DeviceError(
                        f"服务「{part}」在自定义服务与预定义服务中均不存在。"
                        "请改用设备上已有的服务名，或指定端口（如 TCP5211），"
                        "系统将自动创建自定义服务后再下发变更")
                continue
            proto, ports = spec
            reuse = next((s.name for s in custom.values()
                          if s.protocol.upper() == proto and s.ports == ports), None)
            if reuse:
                parts[i] = reuse
                changed = True
                continue
            name = f"{proto}_{ports}"
            while name in custom:   # 同名但端口不同（罕见）：追加序号避让
                name = f"{name}_2"
            await self.apply_change(ChangeOp(
                op="create", resource="service", target_id="",
                data={"name": name, "protocol": proto, "ports": ports,
                      "comment": "访问控制策略引用，由 Agent 自动创建"}))
            custom[name] = ServiceConfig(id="", name=name, protocol=proto, ports=ports)
            parts[i] = name
            changed = True
        if changed:
            data["service"] = ",".join(parts)

    @staticmethod
    def _acl_action_int(value) -> int:
        """动作 → 设备原生 uint32（API 文档：0=拒绝，1=允许）。"""
        if isinstance(value, int):
            return 1 if value >= 1 else 0
        return 1 if str(value).strip().lower() in ("allow", "permit", "accept", "1") else 0

    async def _acl_raw_rule(self, rule_id: str) -> dict:
        """按 uuid 取设备上的原生应用控制策略（更新时的基底，保证未改字段格式正确）。"""
        data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}/appcontrols/policys",
                                   {"_start": 0, "_length": MAX_PAGE_LENGTH})
        for r in self._rows(data):
            if r.get("uuid") == rule_id or r.get("id") == rule_id:
                return r
        raise DeviceError(f"未找到应用控制策略 {rule_id}，可能已被删除，请刷新策略列表后重试")

    @classmethod
    def _acl_native_payload(cls, raw: dict, data: dict) -> dict:
        """把发生变化的扁平字段翻译后合并到设备原生策略结构（update 用）。

        以设备当前原生规则为基底，仅覆盖与当前扁平值不同的字段——未修改字段保持
        设备原样，避免展示值（如"全部"）无法逆翻译、或类型偏差触发"参数类型不匹配"。
        """
        import copy as _copy
        payload = _copy.deepcopy(raw)
        cur = cls._acl_flat(raw)
        src = payload["src"] if isinstance(payload.get("src"), dict) else {}
        dst = payload["dst"] if isinstance(payload.get("dst"), dict) else {}
        src_addrs = src["srcAddrs"] if isinstance(src.get("srcAddrs"), dict) else {}
        dst_addrs = dst["dstAddrs"] if isinstance(dst.get("dstAddrs"), dict) else {}
        advance = payload["advanceOption"] if isinstance(payload.get("advanceOption"), dict) else {}

        def split_list(value) -> list[str]:
            return [p.strip() for p in str(value).replace("，", ",").split(",") if p.strip()]

        def changed(key) -> bool:
            return key in data and str(data.get(key)) != str(cur.get(key))

        if changed("name"):
            payload["name"] = data["name"]
        if changed("enabled"):
            payload["enable"] = bool(data["enabled"])
        if changed("comment"):
            payload["description"] = data.get("comment", "")
        if changed("action"):
            payload["action"] = cls._acl_action_int(data["action"])
        if changed("log"):
            advance["logEnable"] = bool(data["log"])
        if changed("src_zone"):
            src["srcZones"] = split_list(data["src_zone"])
        if changed("dst_zone"):
            dst["dstZones"] = split_list(data["dst_zone"])
        if changed("src_addr"):
            src_addrs.setdefault("srcAddrType", "NETOBJECT")
            src_addrs["srcIpGroups"] = split_list(data["src_addr"])
        if changed("dst_addr"):
            dst_addrs.setdefault("dstAddrType", "NETOBJECT")
            dst_addrs["dstIpGroups"] = split_list(data["dst_addr"])
        if changed("service"):
            dst["services"] = split_list(data["service"])
        if changed("app"):
            dst["applications"] = split_list(data["app"])
        payload["src"], payload["dst"], payload["advanceOption"] = src, dst, advance
        src["srcAddrs"], dst["dstAddrs"] = src_addrs, dst_addrs
        return payload

    @classmethod
    def _acl_create_payload(cls, data: dict) -> dict:
        """扁平字段 → 原生应用控制策略完整载荷（create 用，结构按 API 文档样例）。"""

        def split_list(value) -> list[str]:
            return [p.strip() for p in str(value or "").replace("，", ",").split(",") if p.strip()]

        return {
            "name": str(data.get("name", "")),
            "enable": bool(data.get("enabled", True)),
            "action": cls._acl_action_int(data.get("action", "allow")),
            "description": str(data.get("comment", "")),
            "schedule": "全天",
            "group": "默认策略组",
            "labels": {},
            "src": {"srcZones": split_list(data.get("src_zone")) or {},
                    "srcAddrs": {"srcAddrType": "NETOBJECT",
                                 "srcIpGroups": split_list(data.get("src_addr")) or ["全部"]}},
            "dst": {"dstZones": split_list(data.get("dst_zone")) or {},
                    "dstAddrs": {"dstAddrType": "NETOBJECT",
                                 "dstIpGroups": split_list(data.get("dst_addr")) or ["全部"]},
                    "services": split_list(data.get("service")) or ["any"],
                    "applications": split_list(data.get("app")) or ["全部"]},
            "advanceOption": {"logEnable": bool(data.get("log", False))},
        }

    # ---------- NAT 策略真实设备原生格式 ----------
    async def _raw_native_rule(self, path: str, rule_id: str) -> dict:
        """按 uuid/name 取设备上的原生 NAT 策略（更新时的基底）。"""
        data = await self._request("GET", f"/api/v1/namespaces/{self.namespace}{path}",
                                   {"_start": 0, "_length": MAX_PAGE_LENGTH})
        for r in self._rows(data):
            if r.get("uuid") == rule_id or r.get("id") == rule_id or r.get("name") == rule_id:
                return r
        raise DeviceError(f"未找到 NAT 策略 {rule_id}，可能已被删除，请刷新策略列表后重试")

    @classmethod
    @staticmethod
    def _transfer_addr_str(transfer: dict) -> str:
        """原生 transfer → 转换地址串（兼容 specifyIp 列表/单值与 ipRange 范围）。"""
        translated = transfer.get("specifyIp", "")
        if isinstance(translated, list):
            translated = ",".join(str(x) for x in translated)
        if not translated and isinstance(transfer.get("ipRange"), dict):
            rg = transfer["ipRange"]
            translated = f"{rg.get('start', '')}-{rg.get('end', '')}"
        return str(translated or "")

    @staticmethod
    def _transfer_port_str(transfer: dict) -> str:
        """原生 transfer → 端口串（兼容 specifyPort/port 旧形态与 transferPort 数组）。"""
        port = transfer.get("specifyPort") or transfer.get("port") or ""
        tports = transfer.get("transferPort") or []
        if not port and tports:
            parts = []
            for tp in tports:
                if isinstance(tp, dict):
                    start, end = tp.get("start"), tp.get("end")
                    parts.append(str(start) if start == end else f"{start}-{end}")
                else:
                    parts.append(str(tp))
            port = ",".join(parts)
        return str(port or "")

    def _nat_flat_of_raw(self, raw: dict) -> dict:
        """原生 NAT 策略 → 扁平字段（变更比对用，口径与 get_nat_rules 一致）。"""
        ntype = str(raw.get("natType", "SNAT")).upper()
        body = raw.get("bnat") if ntype == "BNAT" else (raw.get("dnat") or raw.get("snat"))
        body = body if isinstance(body, dict) else {}
        transfer = body.get("transfer") if isinstance(body.get("transfer"), dict) else {}
        translated = self._transfer_addr_str(transfer)
        port = self._transfer_port_str(transfer)
        if ntype == "DNAT":
            dst = self._first((body.get("dstIpobj") or {}).get("specifyIp"), "")
        else:
            dst = self._join(body.get("dstIpGroups"))
        return {
            "name": raw.get("name", ""),
            "enabled": bool(raw.get("enable", True)),
            "comment": raw.get("description", ""),
            "src_zone": self._first(body.get("srcZones"), "any"),
            "src_addr": self._join(body.get("srcIpGroups")),
            "service": self._join(body.get("natService") or body.get("services")),
            "dst_addr": dst or "any",
            "translated_addr": str(translated or ""),
            "translated_port": str(port or ""),
        }

    @staticmethod
    def _split_refs(value) -> list[str]:
        return [p.strip() for p in str(value or "").replace("，", ",").split(",") if p.strip()]

    @classmethod
    def _dst_ipobj(cls, value) -> dict:
        """扁平 dst_addr → 原生 dstIpobj：全为 IP/网段 → IP 列表；否则按 IP组名引用。"""
        parts = cls._split_refs(value)
        if parts and all(cls._ip_like(x) for x in parts):
            return {"dstIpobjType": "IP", "specifyIp": parts}
        return {"dstIpobjType": "IPGROUP", "ipGroups": parts}

    @staticmethod
    def _parse_ports(value) -> list[dict]:
        """'8080' / '8080-8090,9090' → transferPort [{start,end}]。"""
        ports = []
        for part in AfRestClient._split_refs(value):
            m = re.fullmatch(r"(\d{1,5})(?:\s*-\s*(\d{1,5}))?", part)
            if not m:
                continue
            start, end = int(m.group(1)), int(m.group(2) or m.group(1))
            ports.append({"start": start, "end": end})
        return ports

    @classmethod
    def _apply_transfer(cls, transfer: dict, data: dict, cur: dict) -> dict:
        """把 translated_addr/translated_port 的变更合并进原生 transfer 结构。"""
        transfer = transfer if isinstance(transfer, dict) else {}
        if "translated_addr" in data and str(data["translated_addr"]) != str(cur.get("translated_addr")):
            val = str(data["translated_addr"]).strip()
            if "-" in val and not val.count(":"):
                start, _, end = val.partition("-")
                transfer.update({"transferType": "IP_RANGE",
                                 "ipRange": {"start": start.strip(), "end": end.strip()}})
                transfer.pop("specifyIp", None)
                transfer.pop("ipGroups", None)
            elif cls._ip_like(val):
                transfer.update({"transferType": "IP", "specifyIp": val})
                transfer.pop("ipRange", None)
                transfer.pop("ipGroups", None)
            elif val:
                transfer.update({"transferType": "IPGROUP", "ipGroups": cls._split_refs(val)})
                transfer.pop("specifyIp", None)
                transfer.pop("ipRange", None)
        if "translated_port" in data and str(data["translated_port"]) != str(cur.get("translated_port")):
            transfer["transferPort"] = cls._parse_ports(data["translated_port"])
        return transfer

    def _nat_native_payload(self, raw: dict, data: dict) -> dict:
        """把发生变化的扁平字段翻译后合并到设备原生 NAT 结构（update 用）。

        以设备当前原生规则为基底，仅覆盖变更字段；未识别字段保持设备原样。
        BNAT（双向 NAT）结构复杂，仅翻译名称/启停/备注等通用字段。
        """
        import copy as _copy
        payload = _copy.deepcopy(raw)
        cur = self._nat_flat_of_raw(raw)
        ntype = str(raw.get("natType", "SNAT")).upper()

        def changed(key) -> bool:
            return key in data and str(data[key]) != str(cur.get(key))

        if changed("name"):
            payload["name"] = data["name"]
        if changed("enabled"):
            payload["enable"] = bool(data["enabled"])
        if changed("comment"):
            payload["description"] = data.get("comment", "")
        if ntype == "BNAT":
            return payload

        body_key = "dnat" if ntype == "DNAT" else "snat"
        body = payload.get(body_key) if isinstance(payload.get(body_key), dict) else {}
        payload[body_key] = body

        if changed("src_zone"):
            body["srcZones"] = self._split_refs(data["src_zone"])
        if changed("src_addr"):
            body["srcIpGroups"] = self._split_refs(data["src_addr"])
        if changed("service"):
            body["natService"] = self._split_refs(data["service"])
        if ntype == "DNAT":
            if changed("dst_addr"):
                body["dstIpobj"] = self._dst_ipobj(data["dst_addr"])
        else:
            if changed("dst_zone"):
                netobj = body.get("dstNetobj") if isinstance(body.get("dstNetobj"), dict) else {}
                netobj.setdefault("dstNetobjType", "ZONE")
                netobj["zone"] = self._split_refs(data["dst_zone"])
                body["dstNetobj"] = netobj
            if changed("dst_addr"):
                body["dstIpGroups"] = self._split_refs(data["dst_addr"])
        if changed("translated_addr") or changed("translated_port"):
            body["transfer"] = self._apply_transfer(body.get("transfer") or {}, data, cur)
        return payload

    @classmethod
    def _nat_create_payload(cls, data: dict) -> dict:
        """扁平字段 → 原生 NAT 完整载荷（create 用，结构按 API 文档 5.1）。"""
        ntype = str(data.get("type", "SNAT") or "SNAT").upper()
        payload = {
            "name": str(data.get("name", "")),
            "enable": bool(data.get("enabled", True)),
            "natType": ntype,
            "description": str(data.get("comment", "")),
            "schedule": "全天",
        }
        transfer = cls._apply_transfer({}, {"translated_addr": data.get("translated_addr", ""),
                                            "translated_port": data.get("translated_port", "")},
                                       {"translated_addr": "", "translated_port": ""})
        if ntype == "DNAT":
            payload["dnat"] = {
                "srcZones": cls._split_refs(data.get("src_zone")) or ["any"],
                "srcIpGroups": cls._split_refs(data.get("src_addr")) or ["全部"],
                "dstIpobj": cls._dst_ipobj(data.get("dst_addr", "any")),
                "natService": cls._split_refs(data.get("service")) or ["any"],
                "transfer": transfer or {"transferType": "NO_TRANS"},
            }
        else:
            payload["snat"] = {
                "srcZones": cls._split_refs(data.get("src_zone")) or ["any"],
                "srcIpGroups": cls._split_refs(data.get("src_addr")) or ["全部"],
                "dstNetobj": {"dstNetobjType": "ZONE",
                              "zone": cls._split_refs(data.get("dst_zone")) or ["any"]},
                "dstIpGroups": cls._split_refs(data.get("dst_addr")) or ["全部"],
                "natService": cls._split_refs(data.get("service")) or ["any"],
                "transfer": transfer or {"transferType": "OUTIF_IP"},
            }
        return payload

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
