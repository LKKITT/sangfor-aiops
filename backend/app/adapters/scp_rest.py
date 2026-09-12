"""SCP 云计算平台开放 API 适配器（只读接入，EC2 AK/SK 签名认证）。

- 认证：AWS4-HMAC-SHA256（SigV4 变体），Region=cn-south-1、Service=open-api；
  每请求实时签名，无 token 概念；查询串不参与签名（canonical query 恒为空）。
  签名规范化按官方 JS 示例精确复刻：canonicalUri=URL 路径段（不含 query）、
  SignedHeaders=path;x-amz-date（"path" 的值即规范请求第 2 行的 URI——非标准 AWS 写法，
  真机联调若 401 优先核对此处与 Service 名）。
- 仅支持查询类接口；任何变更类操作直接拒绝（apply_change 一律 raise）。
- API 版本前缀按文档各资源推荐版本选择（兼容 SCP 6.8~6.10）：
  clusters=20210725 / servers=20220725 / hosts=20190725 / azs·storages=20200725 /
  platform·overview·host-interfaces=20180725 / classic-bvswitches=20190725。
- 分页：列表响应 data={total_size, page_num, page_size, next_page_num, data[]}，
  next_page_num 为空串表示结束；page_size 上限 100。
- 无配置文件端点：backup_config_file 走基类 NotImplementedError（config_service 已降级处理）。
"""
import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from app.adapters.af_rest import permissive_ssl_context
from app.adapters.base import (
    AclRule, ChangeOp, DeviceClient, DeviceError, DeviceStatus, InterfaceInfo,
    NatRule, NetworkObject, ServiceConfig, StaticRoute, UserBinding,
)
from app.config import settings

# 各资源推荐的 API 版本前缀（字段最全且兼容 SCP 6.8~6.10）
VP_PLATFORM = "20180725"
VP_OVERVIEW = "20180725"
VP_HOST_IF = "20180725"
VP_HOSTS = "20190725"
VP_BVSWITCH = "20190725"
VP_CLUSTERS = "20210725"
VP_AZS = "20200725"
VP_STORAGES = "20200725"
VP_SERVERS = "20220725"

MAX_LIST_ITEMS = 1000        # 列表聚合上限（防大环境拖死请求）
MAX_HOST_INTERFACES = 30     # 网口聚合遍历的物理机上限


class ScpApiClient(DeviceClient):
    device_type = "scp"

    _REGION = "cn-south-1"
    _SERVICE = "open-api"
    _ALGORITHM = "AWS4-HMAC-SHA256"

    def __init__(self, device_id: str, device_name: str, base_url: str,
                 access_key: str, secret_key: str, timeout: float | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.device_id = device_id
        self.device_name = device_name
        self.base_url = base_url.rstrip("/")
        self._access_key = access_key
        self._secret_key = secret_key
        self._timeout = timeout or settings.device_http_timeout
        self._client = httpx.AsyncClient(
            base_url=self.base_url, timeout=self._timeout, transport=transport,
            verify=permissive_ssl_context())   # SCP 平台为自签证书，文档要求关闭校验
        # SCP 为只读接入的能力缺口说明（与 AF 对比）
        self.capability_gaps: dict[str, str] = {
            "nat_rules": "SCP 无 NAT 策略概念，南北向转换由云网关/弹性 IP 负责",
            "acl_rules": "SCP 安全组/网络策略不在本阶段开放接口范围内",
            "static_routes": "SCP 无静态路由端点，路由由 VPC/子网承载",
            "objects": "SCP 无防火墙网络对象概念（地址用 VPC/子网/端口组表达）",
            "services": "SCP 无自定义服务端点",
            "user_bindings": "SCP 无 IP-MAC 绑定概念",
            "zones": "SCP 安全边界用资源池（az）表达，见 get_scp_clusters",
        }

    # ---------- EC2 AK/SK 签名（按官方 JS 示例精确复刻） ----------

    def _auth_header(self, method: str, path: str, body: str = "") -> tuple[str, str]:
        """返回 (Authorization 头, X-Amz-Date 头)。查询串不参与签名。"""
        amzdate = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        datestamp = amzdate[:8]
        hashed_payload = hashlib.sha256((body or "").encode("utf-8")).hexdigest()
        canonical_headers = f"x-amz-date:{amzdate}\n"
        signed_headers = "path;x-amz-date"
        canonical_request = "\n".join(
            [method.upper(), path, "", canonical_headers, signed_headers, hashed_payload])
        hashed_request = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        scope = f"{datestamp}/{self._REGION}/{self._SERVICE}/aws4_request"
        string_to_sign = f"{self._ALGORITHM}\n{amzdate}\n{scope}\n{hashed_request}"
        key = f"AWS4{self._secret_key}".encode("utf-8")
        for msg in (datestamp, self._REGION, self._SERVICE, "aws4_request"):
            key = hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()
        signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        auth = (f"{self._ALGORITHM} Credential={self._access_key}/{scope}, "
                f"SignedHeaders={signed_headers}, Signature={signature}")
        return auth, amzdate

    async def _request(self, method: str, path: str, params: dict | None = None,
                       body: dict | None = None) -> Any:  # noqa: F821
        body_str = json.dumps(body, ensure_ascii=False) if body else ""
        auth, amzdate = self._auth_header(method, path, body_str)
        headers = {"Content-Type": "application/json",
                   "Authorization": auth, "X-Amz-Date": amzdate}
        try:
            if method.upper() == "GET":
                resp = await self._client.get(path, params=params or None, headers=headers)
            else:
                resp = await self._client.request(method.upper(), path,
                                                  params=params or None,
                                                  content=body_str or None, headers=headers)
        except httpx.HTTPError as e:
            raise DeviceError(f"SCP 平台连接失败：{e.__class__.__name__}: {e}") from e
        return self._parse(resp, path)

    @staticmethod
    def _parse(resp: httpx.Response, path: str) -> Any:  # noqa: F821
        if resp.status_code == 401:
            raise DeviceError("SCP 认证失败：请检查 AccessKey/SecretKey")
        if resp.status_code not in (200, 201, 204):
            raise DeviceError(f"SCP 接口 {path} 返回 HTTP {resp.status_code}")
        if resp.status_code == 204 or not (resp.text or "").strip():
            return {}
        try:
            payload = resp.json()
        except ValueError:
            return {}
        if isinstance(payload, dict):
            code = payload.get("code")
            if code not in (None, 0):
                raise DeviceError(payload.get("message") or f"SCP 业务错误 code={code}",
                                  code=code)
            return payload.get("data")
        return payload

    async def _get_list(self, path: str, params: dict | None = None,
                        max_items: int = MAX_LIST_ITEMS) -> list[dict]:
        """分页列表聚合：next_page_num 为空串表示结束。"""
        out: list[dict] = []
        page: dict | None = None
        for _ in range(20):   # 硬上限防死循环
            q = dict(params or {})
            if page is not None:
                q["page_num"] = page.get("next_page_num", "")
            data = await self._request("GET", path, params=q)
            if isinstance(data, list):
                out.extend(data)
                break
            if not isinstance(data, dict):
                break
            out.extend(data.get("data") or [])
            nxt = str(data.get("next_page_num") or "")
            if not nxt or len(out) >= max_items:
                break
            page = {"next_page_num": nxt}
        return out[:max_items]

    # ---------- 会话 ----------

    async def login(self) -> bool:
        """连通性 + 版本验证：/platform 或 /system/version 任一成功即通过。"""
        try:
            platform = await self._request("GET", f"/janus/{VP_PLATFORM}/platform")
            if isinstance(platform, dict) and platform.get("version"):
                return True
        except DeviceError:
            pass
        ver = await self._request("GET", f"/janus/{VP_PLATFORM}/system/version")
        if isinstance(ver, dict) and (ver.get("build_version") or ver.get("custom_version")):
            return True
        raise DeviceError("SCP 连接失败：未能获取平台版本信息，请检查地址与 AK/SK")

    async def keepalive(self) -> bool:
        await self._request("GET", f"/janus/{VP_PLATFORM}/system/version")
        return True

    async def aclose(self) -> None:
        """释放 HTTP 客户端（SCP 无 token/会话概念，仅需关闭连接池）。"""
        try:
            await self._client.aclose()
        except Exception:   # noqa: BLE001
            pass

    # ---------- 状态与资源查询 ----------

    async def get_scp_platform(self) -> dict:
        """平台版本与集群信息（GET /platform，6.8.0+）；低版本降级 /system/version。"""
        try:
            data = await self._request("GET", f"/janus/{VP_PLATFORM}/platform")
        except DeviceError:
            data = {}
        out = dict(data or {})
        if not out.get("version"):
            try:
                ver = await self._request("GET", f"/janus/{VP_PLATFORM}/system/version")
                if isinstance(ver, dict) and ver.get("build_version"):
                    out.setdefault("version", str(ver["build_version"]))
                    patches = ver.get("custom_version")
                    if patches:
                        out["custom_version"] = patches
            except DeviceError:
                pass
        try:
            out["maintain_mode"] = (await self._request(
                "GET", f"/janus/{VP_PLATFORM}/system/maintenance") or {}).get("maintain_mode")
        except DeviceError:
            pass
        return out

    async def _vm_metrics(self, server_id: str) -> dict:
        """单台虚拟机实时使用率（监控接口 cpu.util/memory.util，取最近非空点，单位 %）。"""
        try:
            data = await self._request(
                "GET", f"/janus/{VP_PLATFORM}/metrics/{server_id}",
                params={"object_type": "server", "metric_names": "cpu.util,memory.util",
                        "timegap": "1h"})
        except DeviceError:
            return {}
        out = {}
        for metric, blob in (data or {}).items():
            if not isinstance(blob, dict):
                continue
            vals = [p[1] for p in blob.get("datapoints") or [] if p[1] is not None]
            if vals:
                key = "cpu" if "cpu" in metric else "memory" if "memory" in metric else None
                if key:
                    out[key] = round(float(vals[-1]), 1)
        return out

    async def get_scp_overview(self) -> dict:
        """平台概况：物理资源总量/用量 + 主机/云主机/资源池统计。"""
        return await self._request("GET", f"/janus/{VP_OVERVIEW}/overview") or {}

    async def get_scp_clusters(self) -> list[dict]:
        """集群列表（含归一化资源使用率）。20210725 版本不可用时降级 20180725。"""
        try:
            rows = await self._get_list(f"/janus/{VP_CLUSTERS}/clusters")
        except DeviceError:
            rows = await self._get_list(f"/janus/{VP_PLATFORM}/clusters")
        return [self._norm_cluster(c) for c in rows]

    async def get_scp_hosts(self, cluster_id: str = "", keyword: str = "") -> list[dict]:
        """物理机列表（含归一化资源使用率）。20190725 不可用时降级 20180725。"""
        params: dict = {}
        if cluster_id:
            params["cluster_id"] = cluster_id
        if keyword:
            params["name"] = keyword
        try:
            hosts = await self._get_list(f"/janus/{VP_HOSTS}/hosts", params)
        except DeviceError:
            hosts = await self._get_list(f"/janus/{VP_PLATFORM}/hosts", params)
        if keyword:   # name 过滤服务端未生效时客户端兜底
            hosts = [h for h in hosts
                     if keyword.lower() in str(h.get("name", "")).lower()
                     or keyword in str(h.get("ip", ""))]
        return [self._norm_host(h) for h in hosts]

    async def get_scp_host_interfaces(self, host_id: str) -> list[dict]:
        data = await self._request("GET", f"/janus/{VP_HOST_IF}/hosts/{host_id}/interfaces",
                                   params={"_start": 0, "_length": 100})
        if isinstance(data, dict):
            return data.get("data") or []
        return data or []

    async def get_scp_vms(self, host_id: str = "", status: str = "",
                          keyword: str = "", limit: int = 200) -> list[dict]:
        params: dict = {}
        if host_id:
            params["host_id"] = host_id
        if status:
            params["status"] = status
        vms = await self._get_list(f"/janus/{VP_SERVERS}/servers", params,
                                   max_items=max(limit, 100))
        if keyword:
            kw = keyword.lower()
            vms = [v for v in vms
                   if kw in str(v.get("name", "")).lower()
                   or any(kw in str(ip) for ip in (v.get("ips") or []))
                   or any(kw in str(n.get("ip_address") or "")
                          for n in (v.get("networks") or []))]
        vms = vms[:limit]
        # 平台 servers 列表的 cpu_status/memory_status 恒为 0（不采集），
        # 真实使用率需逐台查监控接口（上限 30 台控制流控与耗时）
        targets = vms[:30]
        sem = asyncio.Semaphore(5)

        async def _fill(vm: dict):
            async with sem:
                m = await self._vm_metrics(str(vm.get("id") or ""))
                if m:
                    vm["realtime_metrics"] = m   # 监控接口已为百分比（unit=%），不经 0-1 修正

        await asyncio.gather(*[_fill(v) for v in targets])
        return [self._norm_vm(v) for v in vms]

    async def get_scp_vm_detail(self, server_id: str) -> dict:
        detail = await self._request("GET", f"/janus/{VP_SERVERS}/servers/{server_id}") or {}
        m = await self._vm_metrics(server_id)
        if m:
            detail["cpu_status"] = {"ratio": m.get("cpu", 0)}
            detail["memory_status"] = {"ratio": m.get("memory", 0)}
            detail["realtime_metrics"] = m   # 监控接口为真实百分比（unit=%）
        return detail

    async def get_scp_storages(self) -> list[dict]:
        """存储列表（含归一化使用率：ratio 缺失时按 total_mb/used_mb 折算）。"""
        rows = await self._get_list(f"/janus/{VP_STORAGES}/storages")
        out = []
        for st in rows:
            st = dict(st)
            res = self._norm_res({"total": st.get("total_mb"),
                                  "used": st.get("used_mb"),
                                  "ratio": st.get("ratio")})
            st["res"] = res
            if not st.get("ratio"):
                st["ratio"] = res["ratio"]
            out.append(st)
        return out

    async def get_scp_bvswitches(self) -> list[dict]:
        """经典网络交换机：桥接网口/端口组（vlan_group+links+phy_if）。"""
        return await self._get_list(f"/janus/{VP_BVSWITCH}/classic-bvswitches")

    @staticmethod
    def _resource_ratio(resources: list[dict], name: str) -> float:
        """从 physical_resources 折算百分比（used/total）。"""
        for r in resources or []:
            if str(r.get("name", "")).lower() == name:
                total, used = float(r.get("total") or 0), float(r.get("used") or 0)
                return round(used / total * 100, 1) if total > 0 else 0.0
        return 0.0

    @staticmethod
    def _norm_res(block: dict | None) -> dict:
        """归一化 cpu/memory/storage 资源块 → {total, used, ratio}。

        平台不同版本字段形态不一（ratio 可能缺失/为 null），ratio 不可用时用 total/used 折算。
        """
        block = block or {}
        total = float(block.get("total_mhz") or block.get("total_mb") or block.get("total") or 0)
        used = float(block.get("used_mhz") or block.get("used_mb") or block.get("used") or 0)
        ratio = block.get("ratio")
        try:
            ratio = float(ratio) if ratio not in (None, "") else None
        except (TypeError, ValueError):
            ratio = None
        if total > 0 and used > 0:
            ratio = used / total * 100
        elif ratio is not None and 0 < ratio <= 1:
            ratio *= 100   # 平台部分版本返回 0-1 比例（如 0.25 = 25%），统一转百分比
        return {"total": total, "used": used,
                "ratio": round(ratio, 1) if ratio else 0.0}

    @classmethod
    def _norm_cluster(cls, c: dict) -> dict:
        out = dict(c)
        out["cpu"] = cls._norm_res(c.get("cpu"))          # 原块替换为归一化（下游体检/报告统一口径）
        out["memory"] = cls._norm_res(c.get("memory"))
        out["storage"] = cls._norm_res(c.get("storage"))
        out["res"] = {"cpu": out["cpu"], "memory": out["memory"], "storage": out["storage"]}
        return out

    @classmethod
    def _norm_host(cls, h: dict) -> dict:
        out = dict(h)
        out["cpu"] = cls._norm_res(h.get("cpu"))
        out["memory"] = cls._norm_res(h.get("memory"))
        out["storage"] = {"total": float((h.get("storage") or {}).get("total_mb") or 0),
                          "used": 0.0, "ratio": 0.0}   # 物理机存储接口无 used 字段
        out["res"] = {"cpu": out["cpu"], "memory": out["memory"], "storage": out["storage"]}
        return out

    @staticmethod
    def _decode_os_type(code: str) -> str:
        """解码 HCI os_type 型号码：首字母为系统族，数字为内核版本。

        文档样例：l2664 = Linux kernel 2.6.64（os_option.kernel_name=kernel-2.6）。
        """
        code = (code or "").strip()
        if not code:
            return ""
        family = {"l": "Linux", "w": "Windows", "o": "Other", "u": "Unix"}.get(code[0].lower())
        digits = code[1:]
        if family and digits.isdigit() and len(digits) >= 3:
            ver = f"{digits[0]}.{digits[1]}.{digits[2:]}"
            return f"Windows Server {digits[:4]}" if family == "Windows" else f"{family} {ver}"
        return family or code

    @classmethod
    def _norm_os(cls, vm: dict) -> str:
        """操作系统展示值：os_name 优先（取 CentOS 7.x/Windows 形态），为空时解码 os_type。"""
        os_name = str(vm.get("os_name") or "").strip()
        if os_name and os_name.lower() not in ("none", "null", "unknown", "n/a", "-", "no os"):
            return os_name
        decoded = cls._decode_os_type(str(vm.get("os_type") or ""))
        return decoded   # 数据源无 OS 信息时由前端显示 "-"

    @classmethod
    def _norm_vm(cls, v: dict) -> dict:
        out = dict(v)
        rt = v.get("realtime_metrics") or {}
        if rt.get("cpu") is not None or rt.get("memory") is not None:
            # 监控接口的真实使用率（已是百分比），直取不经 0-1 修正
            out["cpu_status"] = {"ratio": float(rt.get("cpu") or 0)}
            out["memory_status"] = {"ratio": float(rt.get("memory") or 0)}
        else:
            out["cpu_status"] = cls._norm_res(v.get("cpu_status"))
            out["memory_status"] = cls._norm_res(v.get("memory_status"))
        out["res"] = {"cpu": out["cpu_status"], "memory": out["memory_status"]}
        out["os_display"] = cls._norm_os(v)
        return out

    async def get_status(self) -> DeviceStatus:
        platform = await self.get_scp_platform()
        overview = await self.get_scp_overview()
        phys = overview.get("physical_resources") or []
        servers = overview.get("server") or {}
        hosts = overview.get("host") or {}
        manage_mode = str(platform.get("manage_mode") or "")
        return DeviceStatus(
            sw_version=str(platform.get("version") or "unknown"),
            model={"managed_cloud": "托管云", "private_cloud": "私有云"}.get(manage_mode, manage_mode),
            uptime="",
            cpu_usage=self._resource_ratio(phys, "cpu"),
            memory_usage=self._resource_ratio(phys, "memory"),
            disk_usage=self._resource_ratio(phys, "storage"),
            session_count=int(servers.get("running_count") or 0),
            session_capacity=int(servers.get("total") or 0) or 0,
            mbuf_usage=0.0,
            ha_status="",
            extra={
                "manage_mode": manage_mode,
                "hosts_total": hosts.get("total", 0),
                "hosts_online": hosts.get("online_count", 0),
                "hosts_offline": hosts.get("offline_count", 0),
                "hosts_alarm": hosts.get("alarm_count", 0),
                "servers_total": servers.get("total", 0),
                "servers_running": servers.get("running_count", 0),
                "servers_offline": servers.get("offline_count", 0),
                "servers_alarm": servers.get("alarm_count", 0),
                "storage_ratio": self._resource_ratio(phys, "storage"),
            },
        )

    async def get_interfaces(self) -> list[InterfaceInfo]:
        """聚合全部物理机的网口（zone=功能口类型，功能 IP 取 communication_interface）。"""
        out: list[InterfaceInfo] = []
        hosts = await self.get_scp_hosts()
        sem = asyncio.Semaphore(5)

        async def _one(host: dict):
            async with sem:
                try:
                    return host, await self.get_scp_host_interfaces(host.get("id", ""))
                except DeviceError:
                    return host, []

        results = await asyncio.gather(*[_one(h) for h in hosts[:MAX_HOST_INTERFACES]])
        for host, ifaces in results:
            hname = str(host.get("name") or host.get("ip") or host.get("id") or "")
            for it in ifaces or []:
                funcs = ",".join(it.get("functions") or []) or "business"
                comm = it.get("communication_interface") or []
                func_ips = "; ".join(f"{c.get('function')}:{c.get('ip')}"
                                     for c in comm if c.get("ip"))
                out.append(InterfaceInfo(
                    name=f"{hname}/{it.get('name', '')}",
                    zone=funcs,
                    ip=str(it.get("ip") or ""),
                    netmask=str(it.get("netmask") or ""),
                    extra_ips=func_ips,
                    status="up" if it.get("status") == 1 else "down",
                    speed=str(it.get("speed") or ""),
                    mac=str(it.get("mac") or ""),
                    rx_kbps=0.0, tx_kbps=0.0,
                    comment=str(it.get("description") or ""),
                ))
        return out

    # ---------- 只读边界：不支持的能力 ----------

    async def get_nat_rules(self) -> list[NatRule]:
        return []

    async def get_acl_rules(self) -> list[AclRule]:
        return []

    async def get_user_bindings(self, keyword: str = "") -> list[UserBinding]:
        return []

    async def get_static_routes(self) -> list[StaticRoute]:
        return []

    async def get_network_objects(self) -> list[NetworkObject]:
        return []

    async def get_services(self) -> list[ServiceConfig]:
        return []

    async def apply_change(self, change: ChangeOp) -> dict:
        raise DeviceError("SCP 为只读接入，不支持任何变更类操作")

    # ---------- 配置快照（备份/diff 的统一数据源） ----------

    async def snapshot_config(self) -> dict:
        """SCP 结构化快照：平台/集群/物理机/网口功能 IP/虚拟机 IP 与网卡/桥接端口组/存储。

        保留 AF 语义的空节（objects/services 等），保证备份 diff/恢复预览机制通用。
        """
        platform = await self.get_scp_platform()
        clusters = await self.get_scp_clusters()
        hosts = await self.get_scp_hosts()
        # 网口功能 IP：遍历物理机（上限保护）
        sem = asyncio.Semaphore(5)

        async def _if(host: dict):
            async with sem:
                try:
                    return {"host_id": host.get("id"), "host_name": host.get("name"),
                            "host_ip": host.get("ip"),
                            "interfaces": await self.get_scp_host_interfaces(host.get("id", ""))}
                except DeviceError:
                    return {"host_id": host.get("id"), "host_name": host.get("name"),
                            "host_ip": host.get("ip"), "interfaces": []}

        host_ifs = list(await asyncio.gather(*[_if(h) for h in hosts[:MAX_HOST_INTERFACES]]))
        vms = await self.get_scp_vms(limit=200)
        # 虚拟机瘦身：保留 IP/网卡/端口组关键信息（备份关注点）
        vm_brief = [{
            "id": v.get("id"), "name": v.get("name"), "status": v.get("status"),
            "power_state": v.get("power_state"), "host_id": v.get("host_id"),
            "host_name": v.get("host_name"), "os_name": v.get("os_name"),
            "os_display": v.get("os_display") or "",
            "cpu_status": v.get("cpu_status") or {},
            "memory_status": v.get("memory_status") or {},
            "ips": v.get("ips") or [], "real_ips": v.get("real_ips") or [],
            "networks": [{"mac_address": n.get("mac_address"),
                          "ip_address": n.get("ip_address"),
                          "ipv6_address": n.get("ipv6_address"),
                          "model": n.get("model"), "vif_id": n.get("vif_id"),
                          "network_type": n.get("network_type"),
                          "vpc_name": n.get("vpc_name"), "subnet_name": n.get("subnet_name"),
                          "port_id": n.get("port_id")}
                         for n in (v.get("networks") or [])],
            "cores": v.get("cores"), "memory_mb": v.get("memory_mb"),
            "storage_mb": v.get("storage_mb"),
        } for v in vms]
        storages = await self.get_scp_storages()
        try:
            bvswitches = await self.get_scp_bvswitches()
        except DeviceError:
            bvswitches = []
        return {
            "meta": {"device_id": self.device_id, "device_name": self.device_name,
                     "device_type": self.device_type,
                     "sw_version": str(platform.get("version") or "unknown"),
                     "model": platform.get("manage_mode") or ""},
            "scp_platform": platform,
            "scp_clusters": clusters,
            "scp_hosts": hosts,
            "scp_host_interfaces": host_ifs,
            "scp_vms": vm_brief,
            "scp_storages": storages,
            "scp_bvswitches": bvswitches,
            # AF 语义空节（保持备份/diff/恢复机制通用）
            "objects": [], "services": [], "interfaces": [], "static_routes": [],
            "nat_rules": [], "acl_rules": [], "user_bindings": [],
        }
