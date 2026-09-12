"""HTML 报告生成器：将备份快照 + 配置体检 + 软件更新建议合并为一份自包含 HTML 文件。"""

import json
from datetime import datetime
from html import escape


def _h(text: str) -> str:
    return escape(str(text))


def _table(headers: list[str], rows: list[list], caption: str = "") -> str:
    if not rows:
        return f'<p style="color:#999;font-style:italic">无 {caption} 数据</p>'
    hdr = "".join(f"<th>{_h(h)}</th>" for h in headers)
    bdy = "".join("<tr>" + "".join(f"<td>{_h(c)}</td>" for c in row) + "</tr>" for row in rows)
    cap = f"<caption>{_h(caption)}</caption>" if caption else ""
    return f"<table>{cap}<thead><tr>{hdr}</tr></thead><tbody>{bdy}</tbody></table>"


def _severity_color(s: str) -> str:
    return {"high": "#e74c3c", "medium": "#f39c12", "low": "#3498db"}.get(s, "#999")


def _bool_icon(v) -> str:
    return "✓" if v else "✗"


# ============================================================
# 配置可视化 → HTML
# ============================================================

def _render_status(status: dict) -> str:
    if not status:
        return "<p>无设备状态数据</p>"
    rows = [
        ("软件版本", status.get("sw_version")),
        ("型号", status.get("model")),
        ("运行时间", status.get("uptime")),
        ("CPU 使用率", f"{status.get('cpu_usage', 0)}%"),
        ("内存使用率", f"{status.get('memory_usage', 0)}%"),
        ("磁盘使用率", f"{status.get('disk_usage', 0)}%"),
        ("会话数", f"{status.get('session_count', 0)} / {status.get('session_capacity', 0)}"),
        ("HA 状态", status.get("ha_status", "standalone")),
    ]
    return "<table class=\"kv\">" + "".join(
        f"<tr><th>{_h(k)}</th><td>{_h(v)}</td></tr>" for k, v in rows if v is not None
    ) + "</table>"


def _render_interfaces(interfaces: list[dict]) -> str:
    if not interfaces:
        return ""
    headers = ["名称", "区域", "IP 地址", "掩码", "状态", "速率", "MAC"]
    rows = []
    for i in interfaces:
        extra = f" + {i.get('extra_ips', '')}" if i.get("extra_ips") else ""
        rows.append([
            i.get("name", ""),
            i.get("zone", ""),
            i.get("ip", "") + extra,
            i.get("netmask", ""),
            i.get("status", ""),
            i.get("speed", ""),
            i.get("mac", ""),
        ])
    return _table(headers, rows, "网络接口")


def _render_zones(zones: list[dict]) -> str:
    if not zones:
        return ""
    headers = ["名称", "类型", "安全等级"]
    rows = [[z.get("name", ""), z.get("type", ""), z.get("security_level", "")]
            for z in zones]
    return _table(headers, rows, "安全区域")


def _render_nat(nat_rules: list[dict]) -> str:
    if not nat_rules:
        return ""
    headers = ["名称", "类型", "源区域→目的区域", "源地址→目的地址", "服务", "转换后地址", "转换后端口", "启用"]
    rows = []
    for r in nat_rules:
        rows.append([
            r.get("name", ""),
            r.get("type", ""),
            f"{r.get('src_zone', 'any')}→{r.get('dst_zone', 'any')}",
            f"{r.get('src_addr', 'any')}→{r.get('dst_addr', 'any')}",
            r.get("service", "any"),
            r.get("translated_addr", ""),
            r.get("translated_port", ""),
            _bool_icon(r.get("enabled", True)),
        ])
    return _table(headers, rows, "NAT 策略")


def _render_acl(acl_rules: list[dict]) -> str:
    if not acl_rules:
        return ""
    headers = ["名称", "源区域→目的区域", "源地址→目的地址", "服务", "应用", "动作", "启用", "日志"]
    rows = []
    for r in acl_rules:
        rows.append([
            r.get("name", ""),
            f"{r.get('src_zone', 'any')}→{r.get('dst_zone', 'any')}",
            f"{r.get('src_addr', 'any')}→{r.get('dst_addr', 'any')}",
            r.get("service", "any"),
            r.get("app", "any"),
            r.get("action", ""),
            _bool_icon(r.get("enabled", True)),
            _bool_icon(r.get("log", False)),
        ])
    return _table(headers, rows, "访问控制策略")


def _render_objects(objects: list[dict]) -> str:
    if not objects:
        return ""
    headers = ["名称", "类型", "成员"]
    rows = [[o.get("name", ""), o.get("type", ""), o.get("members", "")]
            for o in objects]
    return _table(headers, rows, "网络对象")


def _render_services(services: list[dict]) -> str:
    if not services:
        return ""
    headers = ["名称", "协议", "端口"]
    rows = [[s.get("name", ""), s.get("protocol", ""), s.get("ports", "")]
            for s in services]
    return _table(headers, rows, "自定义服务")


def _render_routes(routes: list[dict]) -> str:
    if not routes:
        return ""
    headers = ["名称", "目的网段", "下一跳", "出接口", "优先级", "启用"]
    rows = []
    for r in routes:
        rows.append([
            r.get("name", ""),
            r.get("dst", ""),
            r.get("next_hop", ""),
            r.get("interface", ""),
            str(r.get("distance", "")),
            _bool_icon(r.get("enabled", True)),
        ])
    return _table(headers, rows, "静态路由")


def _render_bindings(bindings: list[dict]) -> str:
    if not bindings:
        return ""
    headers = ["用户", "IP", "MAC", "类型", "启用"]
    rows = [[b.get("user", ""), b.get("ip", ""), b.get("mac", ""),
             b.get("binding_type", ""), _bool_icon(b.get("enabled", True))]
            for b in bindings]
    return _table(headers, rows, "用户绑定")


# ---------------- SCP 云计算平台报告 ----------------

def _scp_ratio(block) -> str:
    """资源块使用率文案（兼容 ratio 缺失时 total/used 折算；也接受直接给百分比数字）。"""
    if isinstance(block, (int, float)):
        return f"{float(block):.1f}%"
    block = block or {}
    total = float(block.get("total_mhz") or block.get("total_mb") or block.get("total") or 0)
    used = float(block.get("used_mhz") or block.get("used_mb") or block.get("used") or 0)
    ratio = block.get("ratio")
    if (ratio is None or ratio == "") and total > 0 and used > 0:
        ratio = used / total * 100
    try:
        return f"{float(ratio):.1f}%" if ratio not in (None, "") else "—"
    except (TypeError, ValueError):
        return "—"


def _scp_res(block: dict) -> str:
    block = block or {}
    total = float(block.get("total_mhz") or block.get("total_mb") or block.get("total") or 0)
    used = float(block.get("used_mhz") or block.get("used_mb") or block.get("used") or 0)
    if total <= 0:
        return "—"
    if total >= 1000000:   # mb 大数值转 TB/G 展示
        val = total / 1024 / 1024
        unit = "TB" if val >= 1024 else "GB"
        show = f"{val / 1024:.1f}TB" if val >= 1024 else f"{val:.0f}GB"
        used_v = used / 1024 / 1024
        used_show = f"{used_v / 1024:.1f}TB" if used_v >= 1024 else f"{used_v:.0f}GB"
        return f"{_scp_ratio(block)}（{used_show}/{show}）"
    return _scp_ratio(block)


def render_scp_section(snapshot: dict, status: dict, backup_time: str = "") -> str:
    """SCP 云计算平台：版本信息/集群资源/物理机/虚拟机/网口功能 IP/桥接端口组/存储。"""
    sections = []
    platform = snapshot.get("scp_platform") or {}
    meta = snapshot.get("meta") or {}

    sections.append("<h2>1. 版本信息</h2>")
    sections.append(_table(
        ["项目", "值"],
        [["设备名称", meta.get("device_name", "")],
         ["平台类型", "SCP 云计算平台（纳管 HCI）"],
         ["SCP 版本", platform.get("version") or meta.get("sw_version", "")],
         ["部署模式", {"managed_cloud": "托管云", "private_cloud": "私有云"}.get(
             str(platform.get("manage_mode") or ""), platform.get("manage_mode") or "—")],
         ["集群 IP", (platform.get("dcluster_info") or {}).get("cluster_ip", "—")],
         ["维护模式", "维护中" if platform.get("maintain_mode") else "正常"],
         ["数据时点", f"集群/物理机/虚拟机资源数据为备份时点（{backup_time}）快照，"
                     "与 SCP 控制台实时值可能存在差异"],
         ["实时 CPU / 内存 / 存储",
          f"{_scp_ratio(status.get('cpu_usage') if isinstance(status, dict) else {})} / "
          f"{_scp_ratio(status.get('memory_usage') if isinstance(status, dict) else {})} / "
          f"{_scp_ratio((status.get('extra') or {}).get('storage_ratio') if isinstance(status, dict) else {})}"]],
        caption="平台版本信息"))

    sections.append("<h2>2. 集群资源情况</h2>")
    sections.append(_table(
        ["集群名称", "HCI 版本", "类型", "状态", "CPU（已用/总量）", "内存（已用/总量）", "存储（已用/总量）"],
        [[c.get("name", ""), c.get("version", ""), c.get("type", ""),
          c.get("status", ""), _scp_res(c.get("cpu")), _scp_res(c.get("memory")),
          _scp_res(c.get("storage"))] for c in snapshot.get("scp_clusters") or []],
        caption="集群列表"))

    sections.append("<h2>3. 物理机情况</h2>")
    sections.append(_table(
        ["名称/IP", "所属集群", "状态", "CPU（已用/总量）", "内存（已用/总量）", "存储总量", "告警数", "CPU 型号"],
        [[h.get("name", ""), h.get("cluster_name", ""), h.get("status", ""),
          _scp_res(h.get("cpu")), _scp_res(h.get("memory")),
          f"{float((h.get('storage') or {}).get('total_mb') or 0) / 1024 / 1024:.1f}TB"
          if float((h.get('storage') or {}).get('total_mb') or 0) > 0 else "—",
          h.get("alarm_count", 0),
          (h.get("cpu") or {}).get("type", "—")] for h in snapshot.get("scp_hosts") or []],
        caption="物理机列表"))

    sections.append("<h2>4. 虚拟机情况</h2>")
    vm_rows = []
    for v in snapshot.get("scp_vms") or []:
        ips = ", ".join(v.get("ips") or []) or ", ".join(
            n.get("ip_address") or "" for n in v.get("networks") or [] if n.get("ip_address"))
        cpu_st, mem_st = v.get("cpu_status") or {}, v.get("memory_status") or {}
        vm_cpu = (_scp_ratio(cpu_st)
                  + (f"（{float(cpu_st.get('used_mhz') or 0) / 1000:.1f}GHz/"
                     f"{float(cpu_st.get('total_mhz') or 0) / 1000:.1f}GHz）"
                     if float(cpu_st.get('total_mhz') or 0) > 0 else ""))
        vm_mem = (_scp_ratio(mem_st)
                  + (f"（{float(mem_st.get('used_mb') or 0) / 1024:.1f}/"
                     f"{float(mem_st.get('total_mb') or 0) / 1024:.1f}G）"
                     if float(mem_st.get('total_mb') or 0) > 0 else ""))
        vm_rows.append([v.get("name", ""), v.get("status", ""), ips or "—",
                        v.get("host_name", ""), v.get("os_display") or v.get("os_name") or v.get("os_type") or "—",
                        f"{v.get('cores', '?')}核/{round((v.get('memory_mb') or 0) / 1024, 1)}G/"
                        f"{round((v.get('storage_mb') or 0) / 1024, 0):.0f}G",
                        vm_cpu, vm_mem])
    sections.append(_table(
        ["名称", "状态", "IP", "所在物理机", "操作系统", "规格", "CPU（已用/总量）", "内存（已用/总量）"],
        vm_rows, caption="虚拟机列表"))

    sections.append("<h2>5. 物理机网口与功能 IP</h2>")
    if_rows = []
    for h in snapshot.get("scp_host_interfaces") or []:
        for it in h.get("interfaces") or []:
            funcs = ", ".join(it.get("functions") or [])
            comm = "; ".join(f"{c.get('function')}:{c.get('ip')}"
                             for c in it.get("communication_interface") or [] if c.get("ip"))
            if_rows.append([h.get("host_name", ""), it.get("name", ""), funcs or "—",
                            it.get("ip") or "—", it.get("gateway") or "—",
                            it.get("mac") or "—", comm or "—"])
    sections.append(_table(
        ["物理机", "网口", "功能", "IP", "网关", "MAC", "功能通信 IP"],
        if_rows, caption="物理机网口列表"))

    sections.append("<h2>6. 桥接网卡与端口组</h2>")
    bvs_rows = []
    for b in snapshot.get("scp_bvswitches") or []:
        for vg in b.get("vlan_group") or []:
            bvs_rows.append([b.get("name", ""), vg.get("name", ""), vg.get("type", ""),
                             vg.get("vlan_id", ""), len(vg.get("links") or []),
                             ", ".join(l.get("ipv4") or "" for l in vg.get("links") or [] if l.get("ipv4")) or "—"])
    sections.append(_table(
        ["交换机", "端口组/VLAN 组", "类型", "VLAN ID", "连接数", "关联 IP"],
        bvs_rows, caption="桥接网卡端口组"))

    sections.append("<h2>7. 存储资源</h2>")
    sections.append(_table(
        ["存储名称", "类型", "状态", "容量", "已用", "使用率"],
        [[st.get("name", ""), st.get("type", ""), st.get("status", ""),
          f"{float(st.get('total_mb') or 0) / 1024 / 1024:.1f}TB",
          f"{float(st.get('used_mb') or 0) / 1024 / 1024:.1f}TB",
          _scp_ratio(st)] for st in snapshot.get("scp_storages") or []],
        caption="存储列表"))

    return "".join(sections)


def render_config_section(snapshot: dict, status: dict) -> str:
    """渲染配置可视化（使用备份快照数据 + 实时状态）。"""
    sections = []

    sections.append("<h2>1. 设备状态</h2>")
    sections.append(_render_status(status or snapshot.get("meta", {})))

    interfaces = snapshot.get("interfaces", [])
    if interfaces:
        sections.append("<h2>2. 网络接口</h2>")
        sections.append(_render_interfaces(interfaces))

    zones = snapshot.get("zones", [])
    if zones:
        sections.append("<h2>3. 安全区域</h2>")
        sections.append(_render_zones(zones))

    nat = snapshot.get("nat_rules", [])
    if nat:
        sections.append("<h2>4. NAT 策略</h2>")
        sections.append(_render_nat(nat))

    acl = snapshot.get("acl_rules", [])
    if acl:
        sections.append("<h2>5. 访问控制策略</h2>")
        sections.append(_render_acl(acl))

    objects = snapshot.get("objects", [])
    if objects:
        sections.append("<h2>6. 网络对象</h2>")
        sections.append(_render_objects(objects))

    services = snapshot.get("services", [])
    if services:
        sections.append("<h2>7. 自定义服务</h2>")
        sections.append(_render_services(services))

    routes = snapshot.get("static_routes", [])
    if routes:
        sections.append("<h2>8. 静态路由</h2>")
        sections.append(_render_routes(routes))

    bindings = snapshot.get("user_bindings", [])
    if bindings:
        sections.append("<h2>9. 用户绑定</h2>")
        sections.append(_render_bindings(bindings))

    return "\n".join(sections)


# ============================================================
# 配置体检 → HTML
# ============================================================

def render_checkup_section(checkup: dict) -> str:
    if not checkup:
        return "<p>未执行配置体检</p>"
    score = checkup.get("score", 0)
    grade = checkup.get("grade", "N/A")
    counts = checkup.get("counts", {})
    items = checkup.get("items", [])

    html = [f"""
    <div class="score-box">
        <div class="score-circle" style="background:conic-gradient(#2ecc71 {score}%, #ecf0f1 {score}%)">
            <span>{score}</span>
        </div>
        <div class="score-info">
            <div class="score-grade">等级：{_h(grade)}</div>
            <div class="score-counts">
                <span class="badge" style="background:{_severity_color('high')}">高危 {counts.get('high', 0)}</span>
                <span class="badge" style="background:{_severity_color('medium')}">中危 {counts.get('medium', 0)}</span>
                <span class="badge" style="background:{_severity_color('low')}">低危 {counts.get('low', 0)}</span>
                <span>可自动修复 {checkup.get('auto_fixable', 0)} 项</span>
            </div>
        </div>
    </div>
    """]

    if items:
        html.append("<table><thead><tr><th>级别</th><th>类别</th><th>描述</th><th>建议</th></tr></thead><tbody>")
        for item in items:
            sev = item.get("severity", "low")
            html.append(f"""<tr>
                <td><span class="badge" style="background:{_severity_color(sev)}">{sev}</span></td>
                <td>{_h(item.get('category', ''))}</td>
                <td>{_h(item.get('detail', ''))}</td>
                <td>{_h(item.get('suggestion', ''))}</td>
            </tr>""")
        html.append("</tbody></table>")

    return "".join(html)


# ============================================================
# 软件更新建议 → HTML
# ============================================================

def render_update_section(advice: dict) -> str:
    if not advice:
        return "<p>软件更新建议暂不可用</p>"

    html = [f"""
    <div class="update-summary">
        <div class="update-info">
            <span>当前版本：<b>{_h(advice.get('current_version', ''))}</b></span>
            <span>最新版本：<b>{_h(advice.get('latest_version', ''))}</b></span>
            <span>升级建议：<span class="badge" style="background:{_severity_color(advice.get('risk', 'low'))}">{_h(advice.get('recommendation', ''))}</span></span>
            <span>风险等级：<b>{_h(advice.get('risk', 'low'))}</b></span>
        </div>
    </div>
    """]

    # 架构迁移警告（旧架构→新架构）
    path = advice.get("upgrade_path", {})
    if path.get("cross_arch_migration"):
        html.append(f"""<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:12px 16px;margin:12px 0">
        <div style="font-weight:700;color:#856404;font-size:14px;margin-bottom:4px">⚠ AF 旧架构→新架构迁移提醒</div>
        <div style="color:#856404;font-size:13px;line-height:1.6">
        <p>当前设备为 <b>AF 旧架构版本</b>（≤8.0.50 分界），新架构版本（8.0.48+）采用全新架构设计，<b>不可直接升级</b>。</p>
        <p>升级新版本需按以下流程操作：</p>
        <ol style="margin:6px 0 6px 18px">
            <li>先升级至旧架构末端版本 <b>{_h(path.get("hops", [])[-1]) if path.get("hops") else "8.0.45"}</b>（若未达到）</li>
            <li><b>联系深信服售后</b>执行新旧架构迁移（涉及底层架构变更，需售后专业操作）</li>
            <li>架构迁移完成后，方可升级至 8.0.48+ 新架构版本</li>
        </ol>
        <p style="margin-top:4px">迁移前请务必完成完整配置备份（可在本系统备份页面操作）。</p>
        </div></div>""")

    # 升级路径
    hops = path.get("hops", [])
    if hops:
        html.append(f"<h3>升级路径</h3>")
        html.append(f"<p>{' → '.join(_h(h) for h in hops)}</p>")

    # 升级理由
    reasons = advice.get("reasons", [])
    if reasons:
        html.append("<h3>升级理由</h3>")
        html.append("<table><thead><tr><th>级别</th><th>类型</th><th>说明</th></tr></thead><tbody>")
        for r in reasons:
            lvl = r.get("level", "low")
            html.append(f"<tr><td><span class=\"badge\" style=\"background:{_severity_color(lvl)}\">{_h(lvl)}</span></td>"
                        f"<td>{_h(r.get('type', ''))}</td><td>{_h(r.get('text', ''))}</td></tr>")
        html.append("</tbody></table>")

    # 关键变更
    changes = advice.get("key_changes", {})
    if any(changes.values()):
        html.append("<h3>关键变更</h3>")
        for cat, notes in changes.items():
            if notes:
                html.append(f"<h4>{_h(cat)}</h4>")
                html.append("<ul>" + "".join(f"<li>{_h(n)}</li>" for n in notes) + "</ul>")

    # 升级时机
    timing = advice.get("timing", {})
    if timing:
        html.append("<h3>升级时机</h3>")
        html.append("<ul>" + "".join(f"<li><b>{_h(k)}</b>：{_h(v)}</li>" for k, v in timing.items() if isinstance(v, str)) + "</ul>")
        pre = timing.get("prerequisites", [])
        if pre:
            html.append("<h4>前置条件</h4>")
            html.append("<ul>" + "".join(f"<li>{_h(p)}</li>" for p in pre) + "</ul>")

    # 行动清单
    checklist = advice.get("checklist", [])
    if checklist:
        html.append("<h3>行动清单</h3>")
        html.append("<ol>" + "".join(f"<li><b>步骤 {c.get('step', '')}</b>：{_h(c.get('action', ''))}</li>" for c in checklist) + "</ol>")

    return "".join(html)


# ============================================================
# 组装完整 HTML
# ============================================================

CSS = """
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, "Microsoft YaHei", "Segoe UI", sans-serif; background: #f5f7fa; color: #333; padding: 20px; }
.container { max-width: 1100px; margin: 0 auto; }
.header { background: linear-gradient(135deg, #1a73e8, #0d47a1); color: #fff; padding: 24px 32px; border-radius: 12px; margin-bottom: 20px; }
.header h1 { font-size: 22px; margin-bottom: 6px; }
.header .meta { font-size: 13px; opacity: 0.85; }
.section { background: #fff; border-radius: 10px; padding: 20px 24px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.section h2 { font-size: 17px; color: #1a73e8; border-bottom: 2px solid #e8edf3; padding-bottom: 8px; margin-bottom: 14px; }
.section h3 { font-size: 14px; color: #555; margin: 14px 0 8px; }
.section h4 { font-size: 13px; color: #777; margin: 10px 0 4px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0; }
th, td { border: 1px solid #e8edf3; padding: 6px 8px; text-align: left; }
th { background: #f0f4f9; font-weight: 600; white-space: nowrap; }
td { word-break: break-all; }
tr:hover td { background: #f8fafc; }
caption { caption-side: top; text-align: left; font-weight: 600; padding: 4px 0; color: #555; }
table.kv { width: auto; min-width: 400px; }
table.kv th { width: 100px; background: #f8f9fa; }
ul, ol { margin: 6px 0 6px 20px; font-size: 13px; line-height: 1.6; }
li { margin-bottom: 2px; }
p { font-size: 13px; line-height: 1.6; margin: 6px 0; }
.score-box { display: flex; align-items: center; gap: 20px; margin: 10px 0; }
.score-circle { width: 72px; height: 72px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 22px; font-weight: bold; color: #333; flex-shrink: 0; }
.score-info { display: flex; flex-direction: column; gap: 6px; }
.score-grade { font-size: 16px; font-weight: 600; }
.score-counts { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; font-size: 13px; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; color: #fff; font-size: 11px; font-weight: 600; }
.update-summary { margin: 10px 0; }
.update-info { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; align-items: center; }
.footer { text-align: center; font-size: 11px; color: #999; padding: 16px 0; }
</style>
"""


def generate_report(device_name: str, backup_label: str, backup_time: str,
                    snapshot: dict, status: dict, checkup: dict, update_advice: dict) -> str:
    """生成完整 HTML 报告。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if (snapshot.get("meta") or {}).get("device_type") == "scp":
        config_html = render_scp_section(snapshot, status, backup_time)
    else:
        config_html = render_config_section(snapshot, status)
    checkup_html = render_checkup_section(checkup)
    update_html = render_update_section(update_advice)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>设备配置报告 - {_h(device_name)}</title>
{CSS}
</head>
<body>
<div class="container">

<div class="header">
    <h1>📋 设备配置报告</h1>
    <div class="meta">
        设备：{_h(device_name)} | 备份：{_h(backup_label)} | 备份时间：{_h(backup_time)} | 报告生成：{now}
    </div>
</div>

<div class="section">
    <h2>📊 配置可视化</h2>
    {config_html}
</div>

<div class="section">
    <h2>🏥 配置体检</h2>
    {checkup_html}
</div>

<div class="section">
    <h2>🔄 软件更新建议</h2>
    {update_html}
</div>

<div class="footer">
    由深信服售后技术支持 Agent 自动生成 · 报告生成时间：{now}
</div>

</div>
</body>
</html>"""