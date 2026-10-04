"""离线兜底对话（未配置 LLM API Key 时）：固定意图查询 + 模式引导。

从 orchestrator 拆出的独立模块：纯函数化，便于直测各意图分支。
"""
import re
from typing import AsyncGenerator

from app import db
from app.adapters.factory import get_client


async def offline_reply(conv_id: str, message: str, device_id: str,
                        device: dict) -> AsyncGenerator[dict, None]:
    db.audit("agent.offline_chat", {"message": message[:100]},
             conv_id=conv_id, device_id=device_id)
    text = await offline_answer(message, device_id, device)
    db.add_message(conv_id, "assistant", {"text": text, "tool_calls": []})
    for i in range(0, len(text), 80):
        yield {"type": "token", "text": text[i:i + 80]}
    yield {"type": "offline_notice",
           "text": "（未配置 LLM API Key，当前为离线兜底模式：仅支持固定意图查询，"
                   "完整对话能力请在 backend/.env 配置 LLM_API_KEY）"}
    yield {"type": "done"}


async def offline_answer(message: str, device_id: str, device: dict) -> str:
    from app.agent.orchestrator import GLOBAL_DEVICE_ID   # 延迟导入避免循环依赖
    from app.services.analyzer import run_checks

    if str(device_id or "").startswith("nd_"):
        return ("我是全局运维助手（当前绑定网络设备）。离线兜底模式仅支持深信服设备的固定意图查询，"
                "网络设备对话能力需要配置 LLM API Key 后使用（backend/.env 或『平台设置』中的 LLM_API_KEY）。")
    if device_id == GLOBAL_DEVICE_ID:
        return ("我是全局运维助手（全局模式）。离线兜底模式仅支持绑定具体深信服设备后的固定意图查询，"
                "完整全局对话能力需要配置 LLM API Key 后使用（backend/.env 或『平台设置』中的 LLM_API_KEY）。")
    client = await get_client(device_id)
    m = message.lower()
    if re.search(r"状态|健康|cpu|内存|资源", m):
        s = (await client.get_status()).to_dict()
        return (f"**设备状态**（{device.get('name')}）\n- 软件版本：{s['sw_version']}（{s['model']}）\n"
                f"- CPU：{s['cpu_usage']}%　内存：{s['memory_usage']}%　磁盘：{s['disk_usage']}%\n"
                f"- 会话：{s['session_count']}/{s['session_capacity']}\n"
                f"- 运行时间：{s['uptime']}")
    if re.search(r"接口|网口|端口流量", m):
        rows = [i.to_dict() for i in await client.get_interfaces()]
        lines = ["| 接口 | 区域 | IP | 状态 | 收/发 (kbps) |", "|---|---|---|---|---|"]
        lines += [f"| {r['name']} | {r['zone'] or '-'} | {r['ip'] or '-'} | {r['status']} "
                  f"| {r['rx_kbps']}/{r['tx_kbps']} |" for r in rows]
        return "**网络接口**\n" + "\n".join(lines)
    if re.search(r"网络对象|ip组|ip组|地址组|对象", m) and "更新" not in m and "升级" not in m:
        rows = [o.to_dict() for o in await client.get_network_objects()]
        lines = ["| 对象 | 类型 | 成员 | 备注 |", "|---|---|---|---|"]
        lines += [f"| {r['name']} | {r['type']} | {r['members']} | {r['comment'] or '-'} |"
                  for r in rows]
        return "**网络对象**\n" + "\n".join(lines)
    if re.search(r"自定义服务|服务列表", m) or ("服务" in m and "升级" not in m and "更新" not in m):
        rows = [s.to_dict() for s in await client.get_services()]
        lines = ["| 服务 | 协议 | 端口 | 备注 |", "|---|---|---|---|"]
        lines += [f"| {r['name']} | {r['protocol']} | {r['ports']} | {r['comment'] or '-'} |"
                  for r in rows]
        return "**自定义服务**\n" + "\n".join(lines)
    if re.search(r"nat|地址转换", m):
        rules = [n.to_dict() for n in await client.get_nat_rules()]
        lines = ["| ID | 名称 | 类型 | 源 | 目的 | 服务 | 转换 | 启用 | 命中 |",
                 "|---|---|---|---|---|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['name']} | {r['type']} | {r['src_addr']} | {r['dst_addr']} "
                  f"| {r['service']} | {r['translated_addr']}"
                  f"{'：' + r['translated_port'] if r['translated_port'] else ''} "
                  f"| {'✓' if r['enabled'] else '✗'} | {r['hit_count']} |" for r in rules]
        return "**NAT 策略**\n" + "\n".join(lines)
    if re.search(r"acl|访问控制|策略", m):
        rules = [a.to_dict() for a in await client.get_acl_rules()]
        lines = ["| ID | 名称 | 源 | 目的 | 服务 | 动作 | 启用 | 命中 |",
                 "|---|---|---|---|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['name']} | {r['src_addr']} | {r['dst_addr']} | {r['service']} "
                  f"| {r['action']} | {'✓' if r['enabled'] else '✗'} | {r['hit_count']} |"
                  for r in rules]
        return "**访问控制策略**\n" + "\n".join(lines)
    if re.search(r"绑定", m):
        rows = [b.to_dict() for b in await client.get_user_bindings()]
        lines = ["| 用户 | IP | MAC | 类型 | 启用 |", "|---|---|---|---|---|"]
        lines += [f"| {r['user']} | {r['ip']} | {r['mac'] or '-'} | {r['binding_type']} "
                  f"| {'✓' if r['enabled'] else '✗'} |" for r in rows]
        return "**IP-MAC 绑定**\n" + "\n".join(lines)
    if re.search(r"体检|检查|风险|分析", m):
        report = run_checks(await client.snapshot_config(), (await client.get_status()).to_dict())
        lines = [f"**配置体检**：得分 {report['score']}/100（{report['grade']}），"
                 f"高危 {report['counts']['high']} / 中危 {report['counts']['medium']} / "
                 f"低危 {report['counts']['low']}", ""]
        for item in report["items"][:10]:
            lines.append(f"- 【{item['severity']}】{item['title']} → {item['suggestion']}")
        return "\n".join(lines)
    if re.search(r"备份列表|备份", m):
        rows = db.list_backups(device_id)[:10]
        if not rows:
            return "尚无备份记录，可对我说\"创建备份\"。"
        lines = ["| 备份 | 标签 | 版本 | 时间 |", "|---|---|---|---|"]
        lines += [f"| {r['id']} | {r['label']} | {r['sw_version']} | {r['created_at']} |"
                  for r in rows]
        return "**备份列表**\n" + "\n".join(lines)
    if re.search(r"升级|更新|新版本", m):
        from app.services import upgrade_advisor
        st = await client.get_status()
        advice = await upgrade_advisor.build_upgrade_advice(st.sw_version, st.to_dict(),
                                                            device.get("name", ""))
        return (f"**升级建议**：{advice['recommendation']}\n"
                f"- 当前 {advice['current_version']} → 最新 {advice['latest_version']}\n"
                f"- 升级路径：{' → '.join(advice['upgrade_path']['hops']) or '无需'}\n"
                f"- 时机：{advice['timing']['window']}\n"
                f"- 理由：{'; '.join(r['text'] for r in advice['reasons'])}")
    return ("我是深信服售后技术支持 Agent。当前为**离线兜底模式**，可回答：设备状态 / 接口 / NAT / "
            "访问控制策略 / 用户绑定 / 配置体检 / 备份列表 / 升级建议。"
            "配置 backend/.env 或『平台设置』中的 LLM_API_KEY 后即可使用完整自然语言对话（含配置变更与恢复）。")
