"""AF 模拟器内存状态与种子数据。

种子数据刻意包含典型配置隐患（遮蔽/矛盾/空策略/过宽/高危端口暴露/僵尸规则），
供配置合理性分析引擎与离线演示使用；状态存内存，可通过 API 修改。
"""
import random
import threading
from datetime import datetime, timedelta

DEVICE_VERSION = "8.0.85"
DEVICE_MODEL = "AF-2000-F1000"
DEVICE_SERIAL = "AF2000-6688-DEMO"
DEVICE_ACCOUNTS = {"admin": "Sangfor@123"}   # 模拟器管理员账号（与后端 .env 默认一致）


class SimulatorState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.tokens: dict[str, str] = {}          # token -> username
        self.started_at = datetime.now() - timedelta(days=45)
        self.restore_marker = ""
        self._seed()

    # ---------- 种子配置 ----------
    def _seed(self) -> None:
        self.version = DEVICE_VERSION
        self.model = DEVICE_MODEL
        self.interfaces = [
            {"name": "eth0", "zone": "trust", "ip": "192.168.1.1", "netmask": "255.255.255.0",
             "status": "up", "speed": "1000M", "mac": "00:e0:92:11:00:01", "comment": "内网网关"},
            {"name": "eth1", "zone": "untrust", "ip": "202.96.1.2", "netmask": "255.255.255.248",
             "status": "up", "speed": "1000M", "mac": "00:e0:92:11:00:02", "comment": "电信出口"},
            {"name": "eth2", "zone": "dmz", "ip": "172.16.2.1", "netmask": "255.255.255.0",
             "status": "up", "speed": "1000M", "mac": "00:e0:92:11:00:03", "comment": "服务器区"},
            {"name": "eth3", "zone": "", "ip": "", "netmask": "", "status": "down",
             "speed": "", "mac": "00:e0:92:11:00:04", "comment": "预留（联通出口未启用）"},
        ]
        self.static_routes = [
            {"id": "rt-001", "name": "默认路由", "dst": "0.0.0.0/0", "next_hop": "202.96.1.1",
             "interface": "eth1", "distance": 10, "enabled": True, "comment": ""},
            {"id": "rt-002", "name": "到总部专网", "dst": "10.8.0.0/16", "next_hop": "192.168.1.254",
             "interface": "eth0", "distance": 10, "enabled": True, "comment": "IPSec 对端内网"},
        ]
        # 访问控制（应用控制）策略：含遮蔽/矛盾/过宽/高危端口/僵尸规则
        self.acl_rules = [
            {"id": "acl-001", "name": "允许内网全部上网", "enabled": True, "src_zone": "trust", "dst_zone": "untrust",
             "src_addr": "192.168.0.0/16", "dst_addr": "any", "service": "any", "app": "any",
             "action": "allow", "hit_count": 1582334, "log": False, "comment": "上网基本策略"},
            {"id": "acl-002", "name": "允许服务器区访问外网Web", "enabled": True, "src_zone": "dmz", "dst_zone": "untrust",
             "src_addr": "服务器区网段", "dst_addr": "any", "service": "Web服务,ERP端口", "app": "any",
             "action": "allow", "hit_count": 88211, "log": True, "comment": ""},
            {"id": "acl-003", "name": "禁止财务区访问外网", "enabled": True, "src_zone": "trust", "dst_zone": "untrust",
             "src_addr": "财务网段", "dst_addr": "any", "service": "any", "app": "any",
             "action": "deny", "hit_count": 0, "log": True, "comment": "财务网段禁止出网"},
            {"id": "acl-004", "name": "放行外部访问服务器445", "enabled": True, "src_zone": "untrust", "dst_zone": "dmz",
             "src_addr": "any", "dst_addr": "官网服务器", "service": "TCP/445", "app": "any",
             "action": "allow", "hit_count": 412, "log": False, "comment": "历史遗留：文件共享"},
            {"id": "acl-005", "name": "允许RDP远程到服务器区", "enabled": True, "src_zone": "untrust", "dst_zone": "dmz",
             "src_addr": "any", "dst_addr": "any", "service": "TCP/3389", "app": "any",
             "action": "allow", "hit_count": 1325, "log": True, "comment": "运维远程桌面"},
            {"id": "acl-006", "name": "临时放行-割接测试", "enabled": False, "src_zone": "trust", "dst_zone": "untrust",
             "src_addr": "192.168.99.0/24", "dst_addr": "any", "service": "any", "app": "any",
             "action": "allow", "hit_count": 0, "log": False, "comment": "2025-08 割接临时策略"},
            {"id": "acl-007", "name": "允许内网查询DNS", "enabled": True, "src_zone": "trust", "dst_zone": "untrust",
             "src_addr": "192.168.0.0/16", "dst_addr": "any", "service": "DNS服务", "app": "any",
             "action": "allow", "hit_count": 0, "log": False, "comment": ""},
            {"id": "acl-008", "name": "允许服务器区访问外网Web副本", "enabled": True, "src_zone": "dmz", "dst_zone": "untrust",
             "src_addr": "服务器区网段", "dst_addr": "any", "service": "Web服务", "app": "any",
             "action": "allow", "hit_count": 0, "log": True, "comment": "疑与 acl-002 重复"},
            {"id": "acl-009", "name": "默认拒绝出接口访问", "enabled": True, "src_zone": "any", "dst_zone": "any",
             "src_addr": "any", "dst_addr": "any", "service": "any", "app": "any",
             "action": "deny", "hit_count": 32219, "log": True, "comment": "兜底拒绝"},
        ]
        self.nat_rules = [
            {"id": "nat-001", "name": "内网上网SNAT", "enabled": True, "type": "SNAT",
             "src_zone": "trust", "dst_zone": "untrust", "src_addr": "192.168.0.0/16", "dst_addr": "any",
             "service": "any", "translated_addr": "202.96.1.2", "translated_port": "",
             "hit_count": 2856221, "log": True, "comment": ""},
            {"id": "nat-002", "name": "全网段SNAT", "enabled": True, "type": "SNAT",
             "src_zone": "any", "dst_zone": "untrust", "src_addr": "any", "dst_addr": "any",
             "service": "any", "translated_addr": "202.96.1.2", "translated_port": "",
             "hit_count": 45210, "log": False, "comment": "早期配置，范围过宽"},
            {"id": "nat-003", "name": "发布-官网Web", "enabled": True, "type": "DNAT",
             "src_zone": "untrust", "dst_zone": "untrust", "src_addr": "any", "dst_addr": "202.96.1.2:8080",
             "service": "TCP/8080", "translated_addr": "172.16.2.10:80", "translated_port": "80",
             "hit_count": 75210, "log": True, "comment": "官网对外发布"},
            {"id": "nat-004", "name": "发布-旧运维通道", "enabled": True, "type": "DNAT",
             "src_zone": "untrust", "dst_zone": "untrust", "src_addr": "any", "dst_addr": "202.96.1.2:13389",
             "service": "TCP/13389", "translated_addr": "172.16.2.5:3389", "translated_port": "3389",
             "hit_count": 86, "log": False, "comment": "旧运维系统，建议改VPN"},
            {"id": "nat-005", "name": "DMZ区上网SNAT", "enabled": True, "type": "SNAT",
             "src_zone": "dmz", "dst_zone": "untrust", "src_addr": "服务器区网段", "dst_addr": "any",
             "service": "any", "translated_addr": "202.96.1.2", "translated_port": "",
             "hit_count": 91234, "log": True, "comment": ""},
            {"id": "nat-006", "name": "备用SNAT-联通", "enabled": True, "type": "SNAT",
             "src_zone": "any", "dst_zone": "untrust", "src_addr": "any", "dst_addr": "any",
             "service": "any", "translated_addr": "10.99.1.2", "translated_port": "",
             "hit_count": 0, "log": False, "comment": "联通出口未启用"},
        ]
        self.user_bindings = [
            {"id": "ub-001", "user": "财务-张会计", "ip": "192.168.10.21", "mac": "11:22:33:44:55:01",
             "binding_type": "static", "enabled": True, "comment": ""},
            {"id": "ub-002", "user": "财务-李出纳", "ip": "192.168.10.22", "mac": "11:22:33:44:55:02",
             "binding_type": "static", "enabled": True, "comment": ""},
            {"id": "ub-003", "user": "服务器-Web01", "ip": "172.16.2.10", "mac": "11:22:33:44:66:10",
             "binding_type": "static", "enabled": True, "comment": "官网服务器"},
            {"id": "ub-004", "user": "打印机-3F", "ip": "192.168.1.90", "mac": "11:22:33:44:77:90",
             "binding_type": "static", "enabled": False, "comment": "已报废未清理"},
            {"id": "ub-005", "user": "访客-VLAN99", "ip": "192.168.99.0/24", "mac": "",
             "binding_type": "dynamic", "enabled": True, "comment": "访客网段动态绑定"},
        ]
        self.objects = [
            {"id": "obj-001", "name": "财务网段", "type": "ipgroup", "members": "192.168.10.0/24",
             "comment": "财务部终端"},
            {"id": "obj-002", "name": "服务器区网段", "type": "ipgroup", "members": "172.16.2.0/24",
             "comment": "DMZ 全部服务器"},
            {"id": "obj-003", "name": "官网服务器", "type": "ipgroup", "members": "172.16.2.10",
             "comment": "对外发布主机"},
            {"id": "obj-004", "name": "废弃网段", "type": "ipgroup", "members": "192.168.200.0/24",
             "comment": "已下线业务，未再引用"},
        ]
        self.services = [
            {"id": "svc-001", "name": "Web服务", "protocol": "TCP", "ports": "80,443",
             "comment": "HTTP/HTTPS"},
            {"id": "svc-002", "name": "DNS服务", "protocol": "UDP", "ports": "53",
             "comment": "域名解析"},
            {"id": "svc-003", "name": "ERP端口", "protocol": "TCP", "ports": "8443,9090-9092",
             "comment": "ERP 应用端口段"},
            {"id": "svc-004", "name": "数据库端口", "protocol": "TCP", "ports": "3306,1433",
             "comment": "MySQL/SQL Server，预留"},
            {"id": "svc-005", "name": "遗留服务", "protocol": "TCP", "ports": "8081",
             "comment": "旧系统遗留，未再引用"},
        ]
        # 状态基线（模拟器在基线上做小幅波动）
        self.status_base = {
            "cpu_usage": 34.0, "memory_usage": 63.0, "disk_usage": 47.0,
            "session_count": 4321, "session_capacity": 200000, "mbuf_usage": 76.0,
            "ha_status": "standalone",
        }
        self.traffic_base = {"eth0": (81200, 45600), "eth1": (120300, 92500), "eth2": (32100, 54800), "eth3": (0, 0)}

    # ---------- 动态指标 ----------
    def status_now(self) -> dict:
        rnd = random.Random(int(datetime.now().timestamp() // 20))
        jitter = lambda v, amp: round(max(0.0, min(100.0, v + rnd.uniform(-amp, amp))), 1)
        s = dict(self.status_base)
        s["cpu_usage"] = jitter(s["cpu_usage"], 6)
        s["memory_usage"] = jitter(s["memory_usage"], 2)
        s["mbuf_usage"] = jitter(s["mbuf_usage"], 3)
        s["disk_usage"] = round(s["disk_usage"] + rnd.uniform(-0.1, 0.1), 1)
        s["session_count"] = int(s["session_count"] * (1 + rnd.uniform(-0.05, 0.05)))
        s["sw_version"] = self.version
        s["model"] = self.model
        s["uptime"] = str(datetime.now() - self.started_at).split(".")[0]
        return s

    def traffic_now(self) -> dict:
        rnd = random.Random(int(datetime.now().timestamp() // 10))
        out = {}
        for name, (rx, tx) in self.traffic_base.items():
            f = rnd.uniform(0.85, 1.15)
            out[name] = {"rx_kbps": round(rx * f, 1), "tx_kbps": round(tx * f, 1)}
        return out

    # ---------- 配置文件（模拟私有格式 .conf） ----------
    def render_conf_file(self) -> bytes:
        lines = [f"#SANGFOR-AF-CONF-V{self.version}#", f"#SERIAL={DEVICE_SERIAL}#",
                 f"#EXPORTED={datetime.now().isoformat(timespec='seconds')}#", "#BEGIN-PRIV-DATA#"]
        for section, rows in (("objects", self.objects), ("services", self.services),
                              ("interfaces", self.interfaces), ("static_routes", self.static_routes),
                              ("acl_rules", self.acl_rules), ("nat_rules", self.nat_rules),
                              ("user_bindings", self.user_bindings)):
            lines.append(f"[{section}]")
            for row in rows:
                lines.append("  " + ";".join(f"{k}={v}" for k, v in row.items()))
        lines.append("#END-CONF#")
        return "\n".join(lines).encode("utf-8")

    def next_id(self, prefix: str) -> str:
        with self.lock:
            # 从 100 起计数，避免与种子数据（nat-001~006 等）撞号
            self._id_seq = getattr(self, "_id_seq", 100) + 1
            return f"{prefix}-{self._id_seq:03d}"


STATE = SimulatorState()
