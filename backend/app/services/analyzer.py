"""配置合理性分析引擎（确定性规则引擎，不依赖 LLM）。

检查维度：
- 规则冲突：遮蔽（shadowing）/动作矛盾/重复策略（访问控制与 NAT）
- 空策略与僵尸策略：禁用、匹配域过空、长期零命中
- 过宽权限：any/any 放行、高危端口对公网暴露、过宽 SNAT、DNAT 暴露管理端口
- 资源使用异常：CPU/内存/磁盘/mbuf/会话数阈值（mbuf 对应 AF 8.0.85 已知问题）
- 绑定与路由：重复绑定、静态绑定缺 MAC、缺默认路由

输出风险项（严重度/证据/影响/建议），部分支持"一键修复"（生成结构化 fix plan，
进入 Agent 两阶段确认流执行）。
"""
import ipaddress
from dataclasses import dataclass, field, asdict

SEVERITY_WEIGHT = {"high": 15, "medium": 6, "low": 2}
DANGEROUS_PORTS = {"22": "SSH", "3389": "RDP 远程桌面", "445": "SMB 文件共享",
                   "1433": "SQL Server", "3306": "MySQL", "23": "Telnet", "21": "FTP"}
MGMT_PORTS = {"22", "23", "3389"}
INTERNAL_ZONES = {"trust", "dmz", "内网", "服务器区"}
ANY = {"any", ""}


def _parse_ports(service: str) -> set[str]:
    """从服务/地址串提取端口号：'TCP/445,UDP/53' / '13389' / '172.16.2.5:3389' / 'any'。"""
    ports = set()
    for token in (service or "").replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            tail = token.rsplit(":", 1)[-1]          # 处理 IP:PORT
        elif "/" in token:
            tail = token.split("/")[-1]              # 处理 TCP/PORT
        else:
            tail = token
        if tail.isdigit():
            ports.add(tail)
    return ports


def _parse_tokens(value: str) -> set[str]:
    return {t.strip().upper() for t in (value or "").replace("，", ",").split(",") if t.strip()}


def _addr_covers(a: str, b: str) -> bool:
    """地址 a 的匹配域是否覆盖 b（any 覆盖一切；CIDR 用 ipaddress 判断；对象名仅同名覆盖）。"""
    a, b = (a or "any").strip(), (b or "any").strip()
    if a.lower() in ANY:
        return True
    if b.lower() in ANY:
        return False
    if a == b:
        return True
    try:
        return ipaddress.ip_network(a, strict=False).supernet_of(ipaddress.ip_network(b, strict=False))
    except ValueError:
        return False


def _zone_covers(a: str, b: str) -> bool:
    return (a or "any").strip().lower() in ANY or (a or "") == (b or "")


def _service_covers(a: str, b: str) -> bool:
    a, b = (a or "any").strip(), (b or "any").strip()
    if a.lower() in ANY:
        return True
    if b.lower() in ANY:
        return False
    ta, tb = _parse_tokens(a), _parse_tokens(b)
    return bool(ta) and bool(tb) and ta >= tb


def _rule_covers(outer: dict, inner: dict) -> bool:
    """outer（靠前的规则）匹配域是否覆盖 inner。"""
    if not (outer.get("enabled", True) and inner.get("enabled", True)):
        return False
    app_ok = True
    if "app" in outer and "app" in inner:
        app_ok = _addr_covers(outer.get("app"), inner.get("app"))
    return (_zone_covers(outer.get("src_zone"), inner.get("src_zone"))
            and _zone_covers(outer.get("dst_zone"), inner.get("dst_zone"))
            and _addr_covers(outer.get("src_addr"), inner.get("src_addr"))
            and _addr_covers(outer.get("dst_addr"), inner.get("dst_addr"))
            and _service_covers(outer.get("service"), inner.get("service"))
            and app_ok)


@dataclass
class RiskItem:
    check_id: str
    severity: str            # high / medium / low
    category: str            # 规则冲突 / 空策略 / 过宽权限 / 资源异常 / 绑定与路由
    title: str
    evidence: str            # 风险说明/证据
    suggestion: str          # 修复建议
    rule_ids: list = field(default_factory=list)
    auto_fix: list | None = None   # 结构化修复计划 [{op,resource,target_id,data}]

    def to_dict(self) -> dict:
        return asdict(self)


def _disable_fix(resource: str, rule_id: str) -> list[dict]:
    return [{"op": "update", "resource": resource, "target_id": rule_id,
             "data": {"enabled": False}}]


# ---------------- 策略类检查 ----------------

def check_acl_rules(acl_rules: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    enabled_rules = [r for r in acl_rules if r.get("enabled", True)]
    seen_pairs: set[tuple] = set()

    for i, outer in enumerate(enabled_rules):
        for inner in enabled_rules[i + 1:]:
            if not _rule_covers(outer, inner):
                continue
            if outer.get("action") == inner.get("action"):
                # 同动作：遮蔽/重复
                if _same_match(outer, inner):
                    key = tuple(sorted((outer["id"], inner["id"])))
                    if key not in seen_pairs:
                        seen_pairs.add(key)
                        items.append(RiskItem(
                            "ACL_DUP", "low", "规则冲突",
                            f"策略「{inner['name']}」({inner['id']}) 与「{outer['name']}」({outer['id']}) 匹配条件与动作完全相同",
                            "重复策略徒增维护成本，且可能与原策略改动不同步",
                            f"清理重复项：保留「{outer['name']}」，停用或删除「{inner['name']}」",
                            [inner["id"]], _disable_fix("acl", inner["id"])))
                else:
                    items.append(RiskItem(
                        "ACL_SHADOW", "medium", "规则冲突",
                        f"策略「{inner['name']}」({inner['id']}) 的匹配域被更靠前的「{outer['name']}」({outer['id']}) 完全覆盖（同为 {outer['action']}）",
                        f"设备自上而下匹配，「{inner['name']}」永远不会命中，形同虚设（当前命中 {inner.get('hit_count', 0)} 次）",
                        f"确认业务意图后停用「{inner['name']}」，或将其调整到「{outer['name']}」之前",
                        [inner["id"]], _disable_fix("acl", inner["id"])))
            else:
                # 不同动作：矛盾，内层规则永不生效
                items.append(RiskItem(
                    "ACL_CONFLICT", "high", "规则冲突",
                    f"策略「{inner['name']}」({inner['id']}, {inner['action']}) 被「{outer['name']}」({outer['id']}, {outer['action']}) 覆盖",
                    f"两条策略动作相反，{inner['action']} 永不生效——若这是安全拦截策略，等于防护失效",
                    f"将「{inner['name']}」上移至「{outer['name']}」之前，或收窄「{outer['name']}」的匹配域排除 {inner.get('src_addr')}",
                    [outer["id"], inner["id"]], None))

        # 过宽权限
        if outer.get("action") == "allow":
            broad_src = str(outer.get("src_addr", "")).lower() in ANY and str(outer.get("src_zone", "")).lower() in ANY
            broad_dst = str(outer.get("dst_addr", "")).lower() in ANY and str(outer.get("dst_zone", "")).lower() in ANY
            if broad_src and broad_dst and str(outer.get("service", "")).lower() in ANY:
                items.append(RiskItem(
                    "ACL_ANY_ANY", "high", "过宽权限",
                    f"策略「{outer['name']}」({outer['id']}) 为 any→any 全放行",
                    "任意源到任意目的全部放行，绕过所有细粒度管控，是横向移动的温床",
                    "按最小权限原则拆分为具体网段/服务；如确需全放行请明确业务理由并留档",
                    [outer["id"]], None))
            # 内外网互访全放行（多区域 any 服务，如真实设备常见的 lan,wan→lan,wan 全放行）
            zones_src = {z.strip().lower() for z in str(outer.get("src_zone", "")).split(",") if z.strip()}
            zones_dst = {z.strip().lower() for z in str(outer.get("dst_zone", "")).split(",") if z.strip()}
            if str(outer.get("service", "")).lower() in ANY and \
                    ({"lan", "wan"} <= zones_src or {"lan", "wan"} <= zones_dst or
                     (zones_src and zones_dst and zones_src & {"trust", "lan"} and zones_dst & {"untrust", "wan"})):
                items.append(RiskItem(
                    "ACL_ALL_ZONE_ALLOW", "high", "过宽权限",
                    f"策略「{outer['name']}」({outer['id']}) 对 {'/'.join(sorted(zones_src))} → {'/'.join(sorted(zones_dst))} 的全部服务放行",
                    "内外网全服务放行等于没有访问控制，任意终端可访问任意服务，暴露面最大",
                    "按业务需要收敛为具体的服务与网段白名单；确需全放行的场景应开启日志并配合入侵防护",
                    [outer["id"]], None))
            # 高危端口暴露
            if _from_external(outer):
                for port in _parse_ports(outer.get("service", "")) | _parse_ports(outer.get("dst_addr", "")):
                    if port in DANGEROUS_PORTS:
                        items.append(RiskItem(
                            "ACL_DANGEROUS_PORT", "high", "过宽权限",
                            f"策略「{outer['name']}」({outer['id']}) 允许来自外网的 {DANGEROUS_PORTS[port]}(端口 {port}) 访问（目的 {outer.get('dst_addr')}）",
                            f"端口 {port} 是勒索软件/爆破攻击的高频入口，直接对公网开放风险极高",
                            "关闭该放行并改用 VPN 接入；若业务必需，请限定源 IP 白名单并开启日志与安全防护",
                            [outer["id"]], _disable_fix("acl", outer["id"])))
            if _from_external(outer) and not outer.get("log", False):
                items.append(RiskItem(
                    "ACL_NO_LOG", "medium", "空策略",
                    f"策略「{outer['name']}」({outer['id']}) 放行外网入站流量但未开启日志",
                    "无法回溯访问来源，安全事件发生后缺少取证依据",
                    "为该策略开启日志记录",
                    [outer["id"]], [{"op": "update", "resource": "acl", "target_id": outer["id"],
                                     "data": {"log": True}}]))

    for r in acl_rules:
        if not r.get("enabled", True):
            items.append(RiskItem(
                "ACL_DISABLED", "low", "空策略",
                f"策略「{r['name']}」({r['id']}) 处于停用状态（{r.get('comment') or '未注明原因'}）",
                "长期滞留的停用策略易被误启用或遗忘",
                "确认无用后删除；临时策略请在备注中写明失效时间",
                [r["id"]], None))
        elif r.get("hit_count", 0) == 0 and str(r.get("action")) == "allow":
            items.append(RiskItem(
                "ACL_ZOMBIE", "low", "空策略",
                f"策略「{r['name']}」({r['id']}) 启用中但历史命中为 0",
                "零命中策略可能对应已下线的业务，占据匹配链并增加排查成本",
                "结合业务确认后停用（注意：新增策略短期零命中属正常）",
                [r["id"]], _disable_fix("acl", r["id"])))
    return items


def _check_bnat(nat_rules: list[dict]) -> list[RiskItem]:
    """检查BNAT（双向NAT）策略：内网地址通过防火墙外网接口访问内网服务器的双向地址转换。
    不显示规则ID，使用人类可读描述。"""
    items: list[RiskItem] = []
    bnat_rules = [r for r in nat_rules if r.get("type") == "BNAT" and r.get("enabled", True)]
    for r in bnat_rules:
        name = r.get("name", "未命名")
        service = r.get("service", "any")
        src_addr = r.get("src_addr", "any")
        dst_addr = r.get("dst_addr", "any")
        translated = r.get("translated_addr", "")
        desc_parts = [f"双向NAT策略「{name}」"]
        if src_addr and src_addr != "any":
            desc_parts.append(f"源地址 {src_addr}")
        if dst_addr and dst_addr != "any":
            desc_parts.append(f"外网地址 {dst_addr}")
        if translated:
            desc_parts.append(f"映射到内部 {translated}")
        if service and service != "any":
            desc_parts.append(f"服务 {service}")
        evidence = "，".join(desc_parts)
        items.append(RiskItem(
            "NAT_BNAT", "medium", "双向NAT",
            evidence,
            "BNAT 同时匹配入方向和出方向流量，内网用户可通过外网口地址访问内网服务器，需确认访问范围和源地址是否合理收敛",
            "核查源地址范围是否按需收敛，避免内网任意IP均可通过外网地址访问内部服务器",
            [], None))
    return items


def check_nat_rules(nat_rules: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    items += _check_bnat(nat_rules)
    enabled = [r for r in nat_rules if r.get("enabled", True)]
    for i, outer in enumerate(enabled):
        if outer.get("type") == "SNAT" and str(outer.get("src_addr", "")).lower() in ANY \
                and str(outer.get("src_zone", "")).lower() in ANY:
            items.append(RiskItem(
                "NAT_BROAD", "medium", "过宽权限",
                f"SNAT「{outer['name']}」({outer['id']}) 对任意源地址做地址转换（→{outer.get('translated_addr')}）",
                "过宽 SNAT 会让非计划网段意外借道出网，且难以追溯",
                "将源地址收敛为具体网段；出口地址按业务线拆分",
                [outer["id"]], None))
        if outer.get("type") == "DNAT":
            ports = _parse_ports(outer.get("translated_addr", "")) | _parse_ports(outer.get("service", ""))
            for port in ports:
                if port in MGMT_PORTS:
                    items.append(RiskItem(
                        "NAT_MGMT_EXPOSE", "high", "过宽权限",
                        f"DNAT「{outer['name']}」({outer['id']}) 将公网端口映射到内部 {DANGEROUS_PORTS.get(port, '管理端口')}(端口 {port})：{outer.get('dst_addr')} → {outer.get('translated_addr')}",
                        "管理服务直连公网极易被爆破/勒索利用",
                        "改用 VPN 或堡垒机接入；短期可先停用该映射",
                        [outer["id"]], _disable_fix("nat", outer["id"])))
        for inner in enabled[i + 1:]:
            if outer.get("type") == inner.get("type") and _rule_covers(outer, inner) \
                    and outer.get("translated_addr") == inner.get("translated_addr"):
                items.append(RiskItem(
                    "NAT_SHADOW", "medium", "规则冲突",
                    f"{outer.get('type')}「{inner['name']}」({inner['id']}) 被「{outer['name']}」({outer['id']}) 覆盖（转换地址相同）",
                    "靠后的 NAT 策略永远匹配不到，属于无效配置",
                    "确认后停用被遮蔽的策略",
                    [inner["id"]], _disable_fix("nat", inner["id"])))
    for r in nat_rules:
        if r.get("enabled", True) and r.get("hit_count", 0) == 0 and str(r.get("src_addr", "")).lower() not in ANY:
            items.append(RiskItem(
                "NAT_ZOMBIE", "low", "空策略",
                f"NAT「{r['name']}」({r['id']}) 启用中但命中为 0",
                "可能对应已下线链路（如未启用的联通出口）",
                "确认链路状态后停用或删除",
                [r["id"]], None))
    return items


# ---------------- 资源与基础配置检查 ----------------

def check_status(status: dict, device_type: str = "") -> list[RiskItem]:
    items: list[RiskItem] = []
    scp = device_type == "scp"   # SCP 为云计算平台：无 mbuf/会话数（防火墙语义）
    checks = [
        ("cpu_usage", 85, 70, "CPU 使用率", "设备 CPU 持续高负载会引发丢包、登录卡顿甚至主备切换"),
        ("memory_usage", 85, 70, "内存使用率", "内存耗尽可能触发进程重启"),
        ("disk_usage", 90, 80, "磁盘使用率", "磁盘写满会导致日志记录失败、系统异常"),
    ] + ([] if scp else [
        ("mbuf_usage", 75, 60, "mbuf 缓冲池占用", "mbuf 占满会导致新会话建立失败（AF 8.0.85 存在已知 mbuf 占满问题，8.0.107 彻底修复）"),
    ])
    for key, hi, mid, name, impact in checks:
        v = float(status.get(key, 0) or 0)
        if v >= hi:
            items.append(RiskItem(f"RES_{key.upper()}", "high", "资源异常",
                                  f"{name} {v}%，超过严重阈值 {hi}%", impact,
                                  f"排查高耗进程/会话来源；必要时扩容或升级版本（{name} 参考深信服已知问题清单）", [], None))
        elif v >= mid:
            items.append(RiskItem(f"RES_{key.upper()}", "medium", "资源异常",
                                  f"{name} {v}%，超过预警阈值 {mid}%", impact,
                                  "纳入观察，结合流量趋势判断是否需要扩容", [], None))
    sess, cap = int(status.get("session_count", 0) or 0), int(status.get("session_capacity", 0) or 0)
    if not scp and cap and sess / cap >= 0.7:
        pct = round(sess / cap * 100, 1)
        items.append(RiskItem("RES_SESSION", "medium", "资源异常",
                              f"会话数 {sess}/{cap}（{pct}%）", "会话表接近上限会导致新建连接失败",
                              "排查是否有异常连接（扫描/中毒主机），必要时提升规格", [], None))
    return items


def check_bindings(bindings: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    seen_ip, seen_mac = {}, {}
    for b in bindings:
        if not b.get("enabled", True):
            continue
        ip = str(b.get("ip", ""))
        mac = str(b.get("mac", "")).lower()
        if ip in seen_ip:
            items.append(RiskItem("BIND_DUP_IP", "medium", "绑定与路由",
                                  f"IP {ip} 存在多条生效绑定：{seen_ip[ip]} 与「{b.get('user')}」({b['id']})",
                                  "重复绑定会造成寻址歧义，ARP 防欺骗行为不可预期",
                                  "保留正确一条，清理废弃绑定", [b["id"]], None))
        else:
            seen_ip[ip] = b.get("user", b["id"])
        if mac and mac in seen_mac and seen_mac[mac] != ip:
            items.append(RiskItem("BIND_DUP_MAC", "low", "绑定与路由",
                                  f"MAC {mac} 同时绑定 {seen_mac[mac]} 与 {ip}（{b.get('user')}）",
                                  "一块网卡多 IP 绑定需确认是否为服务器多地址场景",
                                  "确认业务场景，避免误报可忽略", [b["id"]], None))
        elif mac:
            seen_mac[mac] = ip
        if b.get("binding_type") == "static" and not mac:
            items.append(RiskItem("BIND_NO_MAC", "low", "绑定与路由",
                                  f"静态绑定「{b.get('user')}」({b['id']}) 未填写 MAC",
                                  "静态绑定缺 MAC 等于没有绑定，防欺骗失效",
                                  "补全 MAC 或改为动态绑定", [b["id"]], None))
    return items


def check_routes(routes: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    enabled = [r for r in routes if r.get("enabled", True)]
    if not any(str(r.get("dst", "")).startswith("0.0.0.0/") for r in enabled):
        items.append(RiskItem("ROUTE_NO_DEFAULT", "high", "绑定与路由",
                              "未发现启用的默认路由（0.0.0.0/0）",
                              "无默认路由时内网无法访问外网，且双出口场景下切换异常",
                              "确认出口链路并配置默认路由", [], None))
    return items


def _from_external(rule: dict) -> bool:
    zone = str(rule.get("src_zone", "")).lower()
    return zone in ANY or zone in {"untrust", "外网", "wan"} or "wan" in zone.split(",")


def check_rule_conflicts(resource: str, rule: dict, existing: list[dict]) -> list[dict]:
    """定向核实：只检查这一条新增/修改的策略与现有策略的匹配域重叠与动作冲突。

    返回与该规则相关的冲突/重叠项（不涉及任何无关配置）。
    """
    out: list[dict] = []
    rid = str(rule.get("id") or "")
    for other in existing:
        if str(other.get("id")) == rid:      # 修改场景跳过自身
            continue
        if not other.get("enabled", True):
            continue
        overlap = _rule_covers(rule, other) or _rule_covers(other, rule)
        if not overlap:
            continue
        if resource == "acl":
            same = str(rule.get("action", "")).lower() == str(other.get("action", "")).lower()
            out.append({
                "level": "medium" if same else "high",
                "rule_id": other.get("id"), "rule_name": other.get("name"),
                "text": (f"与现有策略「{other.get('name')}」匹配域重叠（{other.get('src_zone', 'any')}:"
                         f"{other.get('src_addr', 'any')} → {other.get('dst_zone', 'any')}:"
                         f"{other.get('dst_addr', 'any')} 服务={other.get('service', 'any')}，"
                         f"动作={other.get('action')}）"),
                "suggestion": ("两者动作相同：本规则可能被靠前的该规则遮蔽或造成重复"
                               if same else
                               "两者动作相反：设备自上而下匹配，若该规则靠前，本策略可能永不生效"),
            })
        elif resource == "nat":
            same_addr = str(rule.get("translated_addr", "")) == str(other.get("translated_addr", ""))
            out.append({
                "level": "low" if same_addr else "medium",
                "rule_id": other.get("id"), "rule_name": other.get("name"),
                "text": (f"与现有{rule.get('type', 'SNAT')}策略「{other.get('name')}」匹配域重叠"
                         f"（源 {other.get('src_zone', 'any')}:{other.get('src_addr', 'any')}，"
                         f"转换→{other.get('translated_addr', '')}）"),
                "suggestion": ("转换地址相同，靠后规则不会命中，属冗余"
                               if same_addr else
                               "匹配域重叠但转换地址不同，实际命中的是靠前的策略，请确认顺序符合预期"),
            })
    return out


def _same_match(a: dict, b: dict) -> bool:
    keys = ("src_zone", "dst_zone", "src_addr", "dst_addr", "service", "app")
    return all(str(a.get(k, "")).lower() == str(b.get(k, "")).lower() for k in keys)


# ---------------- 对象/服务与基础配置检查 ----------------

def _text_referenced(name: str, rules: list[dict], fields=("src_addr", "dst_addr", "service",
                                                           "app", "translated_addr")) -> bool:
    """对象/服务名是否被任意策略字段引用（含逗号分隔成员匹配）。"""
    for r in rules:
        for f in fields:
            val = str(r.get(f, "") or "")
            if name and name in {t.strip() for t in val.replace("，", ",").split(",")}:
                return True
    return False


def _is_builtin_entry(entry: dict) -> bool:
    """设备系统预置的对象/服务（uuid 大段为 0 或 isdefault 标记，如『全部』『any』）不参与未引用/过宽检查。"""
    if entry.get("isdefault"):
        return True
    uid = str(entry.get("id") or entry.get("uuid") or "")
    if not uid or len(uid) < 16:
        return False
    return len(uid.replace("0", "")) <= 4


def check_objects(objects: list[dict], acl_rules: list[dict], nat_rules: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    all_rules = acl_rules + nat_rules
    for o in objects:
        if _is_builtin_entry(o):
            continue
        name = str(o.get("name", ""))
        if not _text_referenced(name, all_rules):
            items.append(RiskItem(
                "OBJ_UNREFERENCED", "low", "空策略",
                f"网络对象「{name}」({o.get('id')}) 未被任何策略引用（{o.get('comment') or '未注明用途'}）",
                "长期未引用的对象徒增维护成本，且可能被误用于新策略",
                "确认无用后删除；在用对象建议在备注中说明引用场景",
                [o.get("id", "")], None))
        for member in str(o.get("members", "")).replace("，", ",").split(","):
            member = member.strip()
            try:
                network = ipaddress.ip_network(member, strict=False)
            except ValueError:
                continue
            if network.prefixlen == 0:
                items.append(RiskItem(
                    "OBJ_BROAD", "medium", "过宽权限",
                    f"网络对象「{name}」({o.get('id')}) 包含 0.0.0.0/0（全部地址）",
                    "引用该对象的策略实际匹配任意地址，等价于 any，容易被放大权限",
                    "拆分对象，收敛到实际业务网段",
                    [o.get("id", "")], None))
    return items


def check_services(services: list[dict], acl_rules: list[dict], nat_rules: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    all_rules = acl_rules + nat_rules
    for s in services:
        if _is_builtin_entry(s):
            continue
        name = str(s.get("name", ""))
        referenced = _text_referenced(name, all_rules, fields=("service", "app"))
        if not referenced:
            items.append(RiskItem(
                "SVC_UNREFERENCED", "low", "空策略",
                f"自定义服务「{name}」({s.get('id')}) 未被任何策略引用（{s.get('comment') or '未注明用途'}）",
                "长期未引用的服务定义徒增维护成本",
                "确认无用后删除", [s.get("id", "")], None))
        for port in _parse_ports(s.get("ports", "")):
            if port in DANGEROUS_PORTS:
                items.append(RiskItem(
                    "SVC_DANGEROUS_PORT", "medium", "过宽权限",
                    f"自定义服务「{name}」({s.get('id')}) 包含 {DANGEROUS_PORTS[port]}(端口 {port})，请确认引用它的策略是否对公网开放",
                    "高危端口经自定义服务放行时容易被忽视，与直接放行同样危险",
                    "核查引用该服务的策略源/目的区域，收敛暴露范围",
                    [s.get("id", "")], None))
    return items


# ---------------- 汇总 ----------------

# ---------------- SCP 云计算平台体检（集群/物理机/虚拟机/存储资源） ----------------

SCP_VM_ABNORMAL_STATUS = {"alert", "overdue", "lost", "paused_io_error"}
SCP_VM_SHUTDOWN_SECONDS = 7 * 86400   # 关机超过 7 天视为僵尸虚拟机


def _fix_ratio(v) -> float:
    """平台部分版本 ratio 返回 0-1 比例（0.25 = 25%），统一修正为百分比。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return round(f * 100, 1) if 0 < f <= 1 else round(f, 1)


def _scp_res_ratio(block: dict, key: str) -> float:
    """从 cpu/memory/storage 资源块取使用率（ratio 直取+0-1 修正，否则 total/used 折算）。"""
    v = block.get(key) or {}
    if isinstance(v, dict):
        if v.get("ratio") is not None:
            return _fix_ratio(v["ratio"])
        total = float(v.get("total_mhz") or v.get("total_mb") or v.get("total") or 0)
        used = float(v.get("used_mhz") or v.get("used_mb") or v.get("used") or 0)
        return round(used / total * 100, 1) if total > 0 else 0.0
    return 0.0


SCP_CAPACITY_STD = ("业界通用容量标准（VMware/公有云同口径，深信服官方容量规划最佳实践一致）："
                    "持续使用率建议 ≤80%，容量规划预留 20%~30% 余量，关键业务建议 N+1 冗余")


def _scp_res_items(check_id: str, name: str, ratio: float, impact: str,
                   cloud_hint: str = "") -> list[RiskItem]:
    """资源使用率规则：建议结合实际值与余量，并引用业界/原厂容量标准。"""
    out = []
    margin = round(max(0.0, 100.0 - ratio), 1)
    advice = (f"当前 {ratio}%、剩余 {margin}%。{SCP_CAPACITY_STD}"
              + (f" 场景建议：{cloud_hint}" if cloud_hint else ""))
    if ratio >= 90:
        out.append(RiskItem(f"{check_id}_{key_upper(name)}", "high", "资源异常",
                            f"{name}使用率 {ratio}%，超过严重阈值 90%（剩余仅 {margin}%）", impact,
                            f"立即处理：优先将高负载虚拟机热迁移至低负载节点，持续高位则安排扩容。{advice}", [], None))
    elif ratio >= 85:
        out.append(RiskItem(f"{check_id}_{key_upper(name)}", "medium", "资源异常",
                            f"{name}使用率 {ratio}%，超过预警阈值 85%（剩余 {margin}%）", impact,
                            f"纳入容量规划观察，结合虚拟机增长趋势评估扩容。{advice}", [], None))
    return out


def key_upper(name: str) -> str:
    return str(name).upper()


def check_scp_clusters(clusters: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    for c in clusters:
        name = c.get("name") or c.get("id") or "?"
        if c.get("status") and c.get("status") != "normal":
            items.append(RiskItem("SCP_CLUSTER_STATUS", "high", "资源异常",
                                  f"集群「{name}」状态异常：{c.get('status')}",
                                  "集群离线/未授权会导致其上虚拟机失去管理平面（HA/热迁移/控制台均不可用）",
                                  "云计算建议：检查集群管理面服务与授权状态；确认其上虚拟机当前是否正常运行，尽快恢复集群在线", [], None))
        items += _scp_res_items("SCP_CLUSTER_CPU", f"集群「{name}」CPU",
                                _scp_res_ratio(c, "cpu"), "集群计算资源耗尽可能影响虚拟机调度与性能",
                                "开启/检查 DRS 动态资源调度，按负载分布热迁移虚拟机")
        items += _scp_res_items("SCP_CLUSTER_MEM", f"集群「{name}」内存",
                                _scp_res_ratio(c, "memory"), "集群内存耗尽可能触发气球/交换，虚拟机性能下降",
                                "检查内存超分比，必要时扩容内存节点")
        items += _scp_res_items("SCP_CLUSTER_STORAGE", f"集群「{name}」存储",
                                _scp_res_ratio(c, "storage"), "存储写满会导致虚拟机磁盘写入失败（pause）",
                                "清理过期快照/镜像、检查精简置备比例，或扩容存储池")
    return items


def check_scp_hosts(hosts: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    for h in hosts:
        name = h.get("name") or h.get("ip") or h.get("id") or "?"
        if h.get("status") and h.get("status") != "running":
            items.append(RiskItem("SCP_HOST_STATUS", "high", "资源异常",
                                  f"物理机「{name}」不在线（{h.get('status')}）",
                                  "物理机离线会触发其上虚拟机 HA 迁移或停机",
                                  "云计算建议：先确认其上虚拟机是否已通过 HA 迁移恢复运行；再检查电源、管理网连通性与 HCI 服务状态，尽快重新上线", [], None))
        cpu = h.get("cpu") or {}
        mem = h.get("memory") or {}
        items += _scp_res_items("SCP_HOST_CPU", f"物理机「{name}」CPU",
                                _fix_ratio(cpu.get("ratio")), "物理机 CPU 高负载影响其上全部虚拟机性能",
                                "将部分虚拟机热迁移至低负载节点均衡负载，并排查异常虚拟机")
        items += _scp_res_items("SCP_HOST_MEM", f"物理机「{name}」内存",
                                _fix_ratio(mem.get("ratio")), "物理机内存压力可能触发气球/交换，拖慢虚拟机",
                                "检查内存超分比并均衡迁移虚拟机，关键业务虚拟机建议预留独占内存")
        alarm = int(h.get("alarm_count") or 0)
        if alarm > 0:
            items.append(RiskItem("SCP_HOST_ALARM", "medium", "资源异常",
                                  f"物理机「{name}」存在 {alarm} 条未处理告警",
                                  "告警可能指示硬件健康、磁盘寿命或服务异常",
                                  "云计算建议：在 SCP 告警中心逐条确认处理，避免演变为节点离线触发批量 HA 迁移", [], None))
    return items


def check_scp_host_allocation(hosts: list[dict]) -> list[RiskItem]:
    """物理机 CPU/内存分配超分检查（业界口径：CPU 4:1~5:1、内存 ≤1.5，关键业务 1:1）。"""
    items: list[RiskItem] = []
    for h in hosts:
        name = h.get("name") or h.get("ip") or h.get("id") or "?"
        cpu = h.get("cpu") or {}
        total_mhz = float(cpu.get("total_mhz") or 0)
        alloc_mhz = float(cpu.get("allocation_max") or 0)
        if total_mhz > 0 and alloc_mhz > 0:
            cpu_ratio_x = round(alloc_mhz / total_mhz, 1)
            if cpu_ratio_x > 5:
                items.append(RiskItem("SCP_HOST_CPU_OVERALLOC", "medium", "资源异常",
                                      f"物理机「{name}」CPU 分配超分 {cpu_ratio_x}:1",
                                      "CPU 超分过高时高负载虚拟机间会相互争抢，性能抖动",
                                      f"业界常见口径 4:1~5:1；当前 {cpu_ratio_x}:1，"
                                      "建议疏散部分虚拟机或限制其 CPU 上限", [], None))
        mem = h.get("memory") or {}
        total_mb = float(mem.get("total_mb") or 0)
        alloc_mb = float(mem.get("allocation_max") or 0)
        if total_mb > 0 and alloc_mb > 0:
            mem_ratio_x = round(alloc_mb / total_mb, 1)
            if mem_ratio_x > 1.5:
                items.append(RiskItem("SCP_HOST_MEM_OVERALLOC", "medium", "资源异常",
                                      f"物理机「{name}」内存分配超分 {mem_ratio_x}:1",
                                      "内存超分过高时靠气球/交换兜底，虚拟机性能明显下降",
                                      "业界保守口径 ≤1.5、关键业务建议 1:1；当前 "
                                      f"{mem_ratio_x}:1，建议调整内存上限或迁移虚拟机", [], None))
    return items


def check_scp_vm_rightsizing(vms: list[dict]) -> list[RiskItem]:
    """虚拟机 rightsizing：单机 CPU/内存持续高使用的建议升配或迁移。"""
    hot_cpu = [v for v in vms if _res_ratio_of(v.get("cpu_status")) >= 90]
    hot_mem = [v for v in vms if _res_ratio_of(v.get("memory_status")) >= 90]
    items: list[RiskItem] = []
    if hot_cpu:
        names = ", ".join(str(v.get("name") or v.get("id")) for v in hot_cpu[:5])
        items.append(RiskItem("SCP_VM_CPU_HOT", "medium", "资源异常",
                              f"{len(hot_cpu)} 台虚拟机 CPU 使用率 ≥90%（{names}"
                              + ("…" if len(hot_cpu) > 5 else "") + "）",
                              "虚拟机 CPU 持续满载，业务响应变慢",
                              "rightsizing 建议：热添加 vCPU 或升配规格，并排查应用侧异常线程", [], None))
    if hot_mem:
        names = ", ".join(str(v.get("name") or v.get("id")) for v in hot_mem[:5])
        items.append(RiskItem("SCP_VM_MEM_HOT", "medium", "资源异常",
                              f"{len(hot_mem)} 台虚拟机内存使用率 ≥90%（{names}"
                              + ("…" if len(hot_mem) > 5 else "") + "）",
                              "虚拟机内存接近上限，可能触发 OOM 或交换",
                              "rightsizing 建议：热添加内存或升配；数据库类虚拟机建议预留 20% 余量", [], None))
    return items


def _res_ratio_of(block) -> float:
    block = block or {}
    return _fix_ratio(block.get("ratio"))


def check_scp_vms(vms: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    abnormal = [v for v in vms if str(v.get("status") or "") in SCP_VM_ABNORMAL_STATUS]
    if abnormal:
        names = ", ".join(str(v.get("name") or v.get("id")) for v in abnormal[:5])
        items.append(RiskItem("SCP_VM_STATUS", "high", "资源异常",
                              f"{len(abnormal)} 台虚拟机处于异常状态（{names}"
                              + ("…" if len(abnormal) > 5 else "") + "）",
                              "虚拟机业务已受影响（告警/逾期/失联/IO 暂停）",
                              "云计算建议：在 SCP 控制台查看告警详情与控制台截图定位；必要时用快照/备份恢复，IO 暂停优先检查存储池容量与健康度", [], None))
    alarmed = [v for v in vms if (v.get("alarm") or {}).get("alarm")]
    if alarmed:
        names = ", ".join(str(v.get("name") or v.get("id")) for v in alarmed[:5])
        items.append(RiskItem("SCP_VM_ALARM", "medium", "资源异常",
                              f"{len(alarmed)} 台虚拟机存在告警（{names}"
                              + ("…" if len(alarmed) > 5 else "") + "）",
                              "虚拟机告警可能指示 CPU/内存/磁盘 IO 资源不足或运行异常",
                              "云计算建议：按告警类型调整虚拟机规格（热添加 CPU/内存）或迁移到负载较低的物理机", [], None))
    zombies = [v for v in vms
               if v.get("is_stopped") and int(v.get("shutdown_duration") or 0) >= SCP_VM_SHUTDOWN_SECONDS]
    if zombies:
        items.append(RiskItem("SCP_VM_ZOMBIE", "low", "资源异常",
                              f"{len(zombies)} 台虚拟机关机超过 7 天",
                              "长期关机虚拟机仍占用存储空间与租户配额，可能已被遗忘",
                              "云计算建议：与使用方确认后归档或删除，释放存储与配额；保留的建议打标签备注用途", [], None))
    return items


def check_scp_storages(storages: list[dict]) -> list[RiskItem]:
    items: list[RiskItem] = []
    for s in storages:
        name = s.get("name") or s.get("id") or "?"
        if s.get("status") and str(s.get("status")).lower() not in ("normal", "connected", "ok"):
            items.append(RiskItem("SCP_STORAGE_STATUS", "medium", "资源异常",
                                  f"存储「{name}」状态异常：{s.get('status')}",
                                  "存储异常可能导致其上虚拟机磁盘 IO 暂停（pause）",
                                  "云计算建议：检查存储集群健康度、主机连接性与缓存盘状态，必要时联系售后", [], None))
        total, used = float(s.get("total_mb") or 0), float(s.get("used_mb") or 0)
        ratio = round(used / total * 100, 1) if total > 0 else 0.0
        items += _scp_res_items("SCP_STORAGE", f"存储「{name}」", ratio,
                                "存储写满会导致虚拟机磁盘写入失败（pause）",
                                "扩容存储池或清理过期快照/无用镜像")
    return items


def run_checks(snapshot: dict, status: dict, device_type: str = "") -> dict:
    items: list[RiskItem] = []
    items += check_acl_rules(snapshot.get("acl_rules") or [])
    items += check_nat_rules(snapshot.get("nat_rules") or [])
    items += check_objects(snapshot.get("objects") or [],
                           snapshot.get("acl_rules") or [], snapshot.get("nat_rules") or [])
    items += check_services(snapshot.get("services") or [],
                            snapshot.get("acl_rules") or [], snapshot.get("nat_rules") or [])
    items += check_status(status, device_type)
    items += check_bindings(snapshot.get("user_bindings") or [])
    if device_type == "scp":   # SCP 云计算平台：集群/物理机/虚拟机/存储资源体检
        items += check_scp_clusters(snapshot.get("scp_clusters") or [])
        items += check_scp_hosts(snapshot.get("scp_hosts") or [])
        items += check_scp_vms(snapshot.get("scp_vms") or [])
        items += check_scp_storages(snapshot.get("scp_storages") or [])
        items += check_scp_host_allocation(snapshot.get("scp_hosts") or [])
        items += check_scp_vm_rightsizing(snapshot.get("scp_vms") or [])
    if device_type == "af":   # 路由为 AF 语义；AC/SCP 不适用（SCP 无静态路由端点）
        items += check_routes(snapshot.get("static_routes") or [])

    order = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda x: (order[x.severity], x.category))
    score = max(0, 100 - sum(SEVERITY_WEIGHT[i.severity] for i in items))
    grade = "优秀" if score >= 90 else "良好" if score >= 75 else "及格" if score >= 60 else "存在风险"
    counts = {s: sum(1 for i in items if i.severity == s) for s in ("high", "medium", "low")}
    return {
        "meta": snapshot.get("meta", {}),
        "status": status,
        "score": score,
        "grade": grade,
        "counts": counts,
        "items": [i.to_dict() for i in items],
        "auto_fixable": sum(1 for i in items if i.auto_fix),
    }
