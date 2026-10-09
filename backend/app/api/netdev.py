"""网络设备管理 API：SSH 交换机/路由器的设备 CRUD、连接测试、批量并行命令执行、
WebSocket 交互式控制台（浏览器 xterm.js 直连设备 CLI）。"""
import asyncio
import json
import logging
import re

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from app import db
from app.services import netdev_ops_service, netdev_service
from app.services.app_settings import get_llm_config
from openai import AsyncOpenAI

log = logging.getLogger("sangfor-agent.netdev")

router = APIRouter(prefix="/api/netdev", tags=["netdev"])

VENDORS = ("huawei", "h3c", "cisco", "ruijie", "zte", "juniper",
           "aruba", "dell", "tplink", "mikrotik", "nokia", "other")


def _public(d: dict) -> dict:
    """出参脱敏：不回传密码，仅标记是否已配置。"""
    return {**{k: v for k, v in d.items() if k not in ("password", "enable_password")},
            "has_password": bool(d.get("password")),
            "has_enable_password": bool(d.get("enable_password"))}


class NetDevIn(BaseModel):
    name: str
    host: str
    vendor: str = "huawei"
    model: str = ""
    port: int = 22
    username: str = ""
    password: str = ""
    enable_password: str = ""
    group_name: str = ""
    device_id: str = ""   # 编辑已有设备时传入：按 id 更新（口令留空=保留原值）


class BatchIn(BaseModel):
    devices: list[NetDevIn]


class TestIn(BaseModel):
    device_id: str
    timeout: float = 15


class ExecuteIn(BaseModel):
    device_ids: list[str]
    commands: list[str]
    name: str = ""
    timeout: float = 30


@router.get("/devices")
def list_devices(group: str = "") -> list[dict]:
    return [_public(d) for d in db.list_netdev_devices(group=group)]


@router.get("/vendors")
def vendors() -> list[dict]:
    return [{"vendor": v.vendor, "display": v.display, "version_cmd": v.version_cmd,
             "paging_cmd": v.paging_cmd}
            for v in netdev_service.VENDOR_PROFILES.values()]


@router.post("/devices")
def add_device(payload: NetDevIn) -> dict:
    if payload.vendor not in VENDORS:
        raise HTTPException(400, f"不支持的厂家类型：{payload.vendor}")
    if not payload.host.strip():
        raise HTTPException(400, "管理地址不能为空")
    data = payload.model_dump()
    if data.get("device_id"):
        existing = db.get_netdev_device(data["device_id"])
        if not existing:
            raise HTTPException(404, "要编辑的设备不存在")
        # 口令留空 = 保留原值（编辑弹窗出于脱敏不回传口令）
        if not data.get("password"):
            data["password"] = existing["password"]
        if not data.get("enable_password"):
            data["enable_password"] = existing["enable_password"]
        data["id"] = data.pop("device_id")
    else:
        data.pop("device_id", None)
    try:
        rec = db.save_netdev_device(data)
    except Exception as e:   # noqa: BLE001 —— 同 host:port 冲突等
        raise HTTPException(400, f"保存失败：{e}") from e
    return _public(rec)


@router.post("/devices/batch")
def add_devices_batch(payload: BatchIn) -> dict:
    """批量添加：逐行校验保存，单行失败不阻塞其余，返回成功/失败明细。"""
    saved, failed = [], []
    for item in payload.devices:
        if item.vendor not in VENDORS:
            failed.append({"host": item.host, "name": item.name, "reason": f"不支持的厂家：{item.vendor}"})
            continue
        if not item.host.strip():
            failed.append({"host": item.host, "name": item.name, "reason": "管理地址为空"})
            continue
        try:
            saved.append(_public(db.save_netdev_device(item.model_dump())))
        except Exception as e:   # noqa: BLE001
            failed.append({"host": item.host, "name": item.name, "reason": str(e)})
    return {"ok": True, "saved": len(saved), "failed": failed, "devices": saved}


@router.get("/devices/export")
def export_devices() -> dict:
    """导出设备清单为 CSV（含口令列，供备份/迁移后经批量导入恢复）。"""
    import csv as _csv
    import io
    buf = io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(["名称", "厂家", "管理地址", "端口", "用户名", "SSH口令",
                     "提权口令", "分组", "型号"])
    for d in db.list_netdev_devices():
        writer.writerow([d["name"], d["vendor"], d["host"], d["port"], d["username"],
                         d["password"], d["enable_password"], d["group_name"], d["model"]])
    return {"ok": True, "filename": "网络设备导出.csv", "csv": buf.getvalue()}


@router.delete("/devices/{device_id}")
def remove_device(device_id: str) -> dict:
    if not db.get_netdev_device(device_id):
        raise HTTPException(404, "设备不存在")
    db.delete_netdev_device(device_id)
    return {"ok": True}


@router.post("/devices/test")
async def test_device(payload: TestIn) -> dict:
    """连接测试：建立 SSH 会话并执行版本查看命令，返回连通性与关键输出。"""
    device = db.get_netdev_device(payload.device_id)
    if not device:
        raise HTTPException(404, "设备不存在")
    prof = netdev_service.profile_of(device["vendor"])
    result = await netdev_service.run_commands(
        device, [prof.version_cmd], timeout=payload.timeout)
    if not result["ok"]:
        return {"ok": False, "error": result.get("error", "连接失败")}
    db.update_netdev_last_ok(device_id=payload.device_id)
    output = result.get("output", "")
    # 版本命令输出通常很长，截取头部关键信息
    head = "\n".join(output.splitlines()[:14])
    return {"ok": True, "vendor": device["vendor"], "duration": result.get("duration", 0),
            "output": head}


@router.post("/execute")
async def execute(payload: ExecuteIn) -> dict:
    """批量并行执行：选中设备 × 命令列表，返回 task_id 供进度轮询。"""
    if not payload.commands or not [c for c in payload.commands if c.strip()]:
        raise HTTPException(400, "命令列表不能为空")
    devices = []
    for did in payload.device_ids:
        d = db.get_netdev_device(did)
        if not d:
            raise HTTPException(404, f"设备不存在：{did}")
        devices.append(d)
    if not devices:
        raise HTTPException(400, "请至少选择一台设备")
    task_id = netdev_service.start_batch_task(
        payload.name, devices, [c.strip() for c in payload.commands if c.strip()],
        timeout=min(max(payload.timeout, 5), 300))
    return {"ok": True, "task_id": task_id, "total": len(devices)}


@router.get("/tasks")
def tasks(limit: int = 20) -> list[dict]:
    return db.list_netdev_tasks(limit=min(limit, 100))


@router.get("/tasks/{task_id}")
def task_detail(task_id: str) -> dict:
    task = db.get_netdev_task(task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@router.websocket("/ws/{device_id}")
async def ws_terminal(ws: WebSocket, device_id: str) -> None:
    """交互式控制台：浏览器 xterm.js ⇄ WebSocket ⇄ 设备 SSH 会话（双向透传）。

    消息协议（JSON）：in: {type:data,text} / {type:resize,cols,rows}；
    out: {type:data,text} / {type:error,text} / {type:closed}。
    """
    await ws.accept()
    device = db.get_netdev_device(device_id)
    if not device:
        await ws.send_text(json.dumps({"type": "error", "text": "设备不存在"}))
        await ws.close()
        return
    if not netdev_service.SSH_AVAILABLE:
        await ws.send_text(json.dumps({"type": "error", "text": "asyncssh 未安装"}))
        await ws.close()
        return
    try:
        conn = await asyncio.wait_for(asyncssh_connect(device), timeout=12)
        proc = await conn.create_process(term_type="xterm-256color",
                                         term_size=(120, 32))
    except Exception as e:   # noqa: BLE001 —— 认证/网络/参数异常统一友好下发
        log.warning("控制台连接失败 device=%s: %s", device_id, e)
        await ws.send_text(json.dumps({"type": "error", "text": f"SSH 连接失败：{e}"}))
        await ws.close()
        return

    # 连接即关闭分页（如 H3C screen-length disable）：dis cu 等长输出不再停在 ---- More ----。
    # 该命令是会话级显示设置（视图分页），不写入设备配置、不影响其他会话；
    # 失败（如设备不支持/权限不足）静默跳过，用户仍可手动翻页
    if paging_cmd := netdev_service.profile_of(device.get("vendor", "")).paging_cmd:
        try:
            proc.stdin.write(paging_cmd + "\n")
        except Exception:   # noqa: BLE001
            pass

    async def pump_out() -> None:
        try:
            while True:
                data = await proc.stdout.read(4096)
                if not data:
                    break
                await ws.send_text(json.dumps({"type": "data", "text": data}))
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        except Exception:   # noqa: BLE001
            log.debug("控制台输出泵退出 device=%s", device_id, exc_info=True)

    async def pump_in() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if msg.get("type") == "data":
                proc.stdin.write(str(msg.get("text", "")))
            elif msg.get("type") == "resize":
                try:
                    proc.change_terminal_size(int(msg.get("cols") or 120),
                                              int(msg.get("rows") or 32))
                except Exception:   # noqa: BLE001 —— 尺寸变更失败不影响会话
                    pass
            elif msg.get("type") == "close":
                return

    try:
        await asyncio.gather(pump_out(), pump_in())
    except WebSocketDisconnect:
        pass
    finally:
        try:
            conn.close()
        except Exception:   # noqa: BLE001
            pass
        try:
            await ws.close()
        except Exception:   # noqa: BLE001
            pass


def asyncssh_connect(device: dict):
    """独立封装便于测试替换；参数与批量执行保持一致（含老设备传统算法扩展）。"""
    import asyncssh
    return asyncssh.connect(**netdev_service.ssh_connect_kwargs(device))


# ---------------- 运维工作台：配置可视化快照 / 配置体检 / 配置备份（全部只读，无恢复） ----------------

def _require_netdev(device_id: str) -> dict:
    device = db.get_netdev_device(device_id)
    if not device:
        raise HTTPException(404, "网络设备不存在")
    if (device.get("vendor") or "") not in ("huawei", "h3c", "ruijie"):
        raise HTTPException(400, "该厂家暂不支持工作台运维（仅华为/H3C/锐捷）")
    return device


@router.get("/{device_id}/snapshot")
async def netdev_snapshot(device_id: str, force: int = 0) -> dict:
    """配置可视化快照：设备状态/接口/VLAN/路由/ARP/MAC/运行配置（60s 进程内缓存）。"""
    device = _require_netdev(device_id)
    result = await netdev_ops_service.collect_snapshot(device, force=bool(force))
    if not result.get("ok"):
        raise HTTPException(502, result.get("error", "采集失败"))
    return result


@router.post("/{device_id}/checkup")
async def netdev_checkup(device_id: str, force: int = 0) -> dict:
    """配置体检：拉运行配置做规则分析（结果缓存 5 分钟），结构同深信服体检。"""
    device = _require_netdev(device_id)
    result = await netdev_ops_service.run_checkup(device, force=bool(force))
    if not result.get("ok"):
        raise HTTPException(502, result.get("error", "体检失败"))
    return result


@router.get("/{device_id}/backups")
def netdev_backup_list(device_id: str) -> list[dict]:
    _require_netdev(device_id)
    return db.list_backups(device_id)


@router.post("/{device_id}/backups")
async def netdev_backup_create(device_id: str, payload: dict) -> dict:
    """配置备份：拉运行配置全文存档（.conf 文本 + 快照 JSON）。"""
    device = _require_netdev(device_id)
    try:
        rec = await netdev_ops_service.create_backup(device, str(payload.get("label") or ""))
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e
    return {"ok": True, "id": rec["id"], "label": rec["label"],
            "sw_version": rec["sw_version"], "created_at": rec["created_at"]}


@router.get("/{device_id}/backups/diff")
def netdev_backup_diff(device_id: str, a: str, b: str) -> dict:
    _require_netdev(device_id)
    try:
        return netdev_ops_service.diff_backups(a, b)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/{device_id}/backups/{backup_id}/file")
def netdev_backup_file(device_id: str, backup_id: str):
    """下载配置文本归档（.conf）。"""
    from fastapi.responses import FileResponse
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise HTTPException(404, "备份不存在")
    if not backup["file_path"]:
        raise HTTPException(404, "该备份没有配置文件归档")
    return FileResponse(backup["file_path"],
                        filename=backup["file_path"].split("\\")[-1].split("/")[-1])


@router.delete("/{device_id}/backups/{backup_id}")
def netdev_backup_delete(device_id: str, backup_id: str) -> dict:
    _require_netdev(device_id)
    backup = db.get_backup(backup_id)
    if not backup or backup["device_id"] != device_id:
        raise HTTPException(404, "备份不存在")
    if backup.get("file_path"):
        import pathlib
        pathlib.Path(backup["file_path"]).unlink(missing_ok=True)
    db.delete_backup(backup_id)
    return {"ok": True}


# ---------------- 网络拓扑（LLDP/ARP 自动发现 + 分组拓扑 + 资产定位） ----------------

from app.services import netdev_topology_service as topo  # noqa: E402


class TopologyPositionsIn(BaseModel):
    group: str = ""
    positions: dict = {}          # {device_id: [x, y]}


@router.get("/topology")
async def get_topology(group: str = "", force: int = 0) -> dict:
    """分组拓扑：LLDP 链路（优先）+ ARP 资产索引。force=1 强制重新采集（TTL 外自动增量采集）。"""
    if not netdev_service.SSH_AVAILABLE and force:
        raise HTTPException(400, "asyncssh 未安装，无法采集拓扑")
    devices = db.list_netdev_devices(group)
    groups = sorted({d.get("group_name", "") for d in db.list_netdev_devices()})
    if not devices and not group:
        return {"group": "", "groups": groups, "nodes": [], "edges": [],
                "arp_index": [], "positions": {}, "stats": {}, "collect_stats": {}}
    return await topo.topology_payload(group, force=bool(force))


@router.get("/topology/search")
async def search_topology(group: str = "", q: str = "") -> dict:
    """资产定位：IP / MAC / 设备名 → 所在设备与端口。"""
    return topo.search_asset(group, q)


@router.post("/topology/positions")
async def save_topology_positions(payload: TopologyPositionsIn) -> dict:
    """保存手动布局坐标（整组覆盖）。"""
    db.save_netdev_topology_positions(payload.group, payload.positions)
    return {"ok": True}


@router.delete("/topology/positions")
async def clear_topology_positions(group: str = "") -> dict:
    db.save_netdev_topology_positions(group, {})
    return {"ok": True}


# ================= 控制台 AI 辅助：回显分析/排错建议（只读；建议命令不自动执行） =================

class ConsoleAnalyzeIn(BaseModel):
    device_id: str
    output: str = ""
    current_command: str = ""


_OUTPUT_MAX_LINES = 200
_OUTPUT_MAX_CHARS = 12000
_SECRET_HINTS = ("password", "passwd", "secret", "community", "凭证", "pin")


def sanitize_terminal_output(text: str, max_lines: int = _OUTPUT_MAX_LINES,
                             max_chars: int = _OUTPUT_MAX_CHARS) -> str:
    """回显脱敏：疑似口令/密钥行打码；行数与体积截断（控制喂给 LLM 的成本）。"""
    lines = []
    for ln in text.splitlines()[-max_lines:]:
        low = ln.lower()
        if any(h in low for h in _SECRET_HINTS):
            ln = re.sub(r"\S+\s*$", "******", ln)
        lines.append(ln)
    return "\n".join(lines)[-max_chars:]


def build_console_prompt(dev: dict, output: str, current_command: str) -> str:
    prof = netdev_service.profile_of(dev.get("vendor", ""))
    return (
        f"你是资深网络设备排错助手。设备：{prof.display}（管理地址 {dev.get('host', '')}:{dev.get('port', 22)}，"
        f"型号 {dev.get('model') or '未知'}）。\n"
        f"最近执行的命令：{current_command or '（无）'}\n"
        f"终端回显（已脱敏，可能被截断）：\n{output}\n\n"
        "请分析：1) 回显说明设备处于什么状态（正常/异常及依据）；"
        "2) 可能原因按概率排序最多 3 条；"
        "3) 建议下一步命令最多 4 条（仅该厂商 CLI 语法，commands 数组只放命令本身，不要解释）。\n"
        '只输出 JSON：{"analysis": "markdown 文本", "commands": ["命令1", "命令2"]}'
    )


@router.post("/console/analyze")
async def console_analyze(payload: ConsoleAnalyzeIn):
    """控制台 AI 分析：终端回显 → 状态研判 + 可能原因 + 建议命令。

    只读能力：分析文本由前端渲染，建议命令仅“插入终端”由用户人工回车执行，绝不代发。
    """
    dev = db.get_netdev_device(payload.device_id)
    if not dev:
        raise HTTPException(404, "设备不存在")
    output = sanitize_terminal_output(payload.output)
    if not output.strip():
        raise HTTPException(400, "终端回显为空：请先在控制台执行一条命令，再做 AI 分析")
    llm = get_llm_config()
    if not (llm.get("api_key") and llm.get("base_url")):
        raise HTTPException(409, "未配置大模型（平台设置 → 大模型接入），AI 分析不可用")

    client = AsyncOpenAI(api_key=llm["api_key"], base_url=llm["base_url"], timeout=60)
    resp = await client.chat.completions.create(
        model=llm["model"], temperature=0.2, max_tokens=1200,
        messages=[{"role": "system", "content": "你是网络设备排错助手，只输出 JSON。"},
                  {"role": "user", "content": build_console_prompt(dev, output, payload.current_command)}])
    text = (resp.choices[0].message.content or "").strip()
    analysis, commands = text, []
    # 模型常把 JSON 包进 ```json 围栏、且 analysis 值里带真实换行（JSON 规范不允许）——
    # 剥围栏 + strict=False 容忍字符串内控制字符，双保险提升解析成功率
    stripped = re.sub(r"```(?:json)?", "", text).strip()
    try:
        m = re.search(r"\{.*\}", stripped, re.S)
        parsed = json.loads(m.group(0), strict=False) if m else None
    except Exception:   # noqa: BLE001 —— 非 JSON 回退为纯文本分析
        parsed = None
    if isinstance(parsed, dict):
        analysis = str(parsed.get("analysis") or stripped)
        commands = [str(c).strip() for c in (parsed.get("commands") or []) if str(c).strip()][:4]
    else:
        # 宽松提取兜底：模型输出常是"伪 JSON"（analysis 值里带未转义引号/换行，json.loads 必败）。
        # 按字段标记做正则提取，保证 analysis 与 commands 不因格式问题整体丢失。
        m_analysis = re.search(r'"analysis"\s*:\s*"(.*?)"\s*,\s*"commands"', stripped, re.S)
        m_commands = re.search(r'"commands"\s*:\s*\[(.*?)\]', stripped, re.S)
        if m_analysis or m_commands:
            if m_analysis:
                analysis = m_analysis.group(1).replace('\\n', '\n').replace('\\"', '"')
            if m_commands:
                commands = [c.strip().strip('"').replace('\\"', '"')
                            for c in re.split(r'"\s*,\s*"?', m_commands.group(1))
                            if c.strip().strip('"')][:4]
    db.audit("netdev.console.ai",
             {"args": {"device": dev.get("name", ""), "cmd": payload.current_command[:120],
                       "output_chars": len(output)}},
             device_id=payload.device_id)
    return {"analysis": analysis, "commands": commands}


# ================= 控制台 AI 助手：自然语言运维请求 → 规划只读命令或直接作答 =================
# 与 /console/analyze 的分工：analyze 面向"已有回显做研判"；assist 面向"用户意图"，
# 可让前端代为执行只读查询命令（display/show 等）后再基于新回显作答。

class ConsoleAssistIn(BaseModel):
    device_id: str
    request: str
    output: str = ""
    done_commands: list[str] = []       # 本轮已自动执行过的命令（防循环重复执行）
    failed_commands: list[str] = []     # 设备报语法/不存在错误的命令（禁止再规划）
    force_answer: bool = False      # 前端达到自动执行轮次上限时置位：要求直接基于已有回显收尾作答


# 只读白名单：查询类首词 + MikroTik print/monitor 风格；配合危险词黑名单双保险。
# 自动执行是代用户敲回车，必须默认拒绝——不在白单内的一律转人工确认。
_ASSIST_READONLY_ALLOW = re.compile(
    r"^\s*(display|show|screen-length|terminal\s+length|ping|tracert|traceroute|dir|more|head|tail|transceiver)\b"
    r"|^\s*display\s+(counters|packet-drop|optic|diagnostic|environment|transceiver)\b"
    r"|^\s*show\s+(counters|environment|diagnostic|interfaces?\s+transceiver|interface\s+transceiver)\b"
    r"|^\s*/\S+.*\b(print|monitor)\b", re.I)
_ASSIST_DANGEROUS = re.compile(
    r"\b(config|conf\b|undo|reset|reboot|reload|save|delete|copy|debug|clear|shutdown"
    r"|restore|upgrade|erase|write|system-view|patch|license)\b|^no\s", re.I)


def filter_readonly_commands(commands: list[str]) -> tuple[list[str], list[str]]:
    """模型给出的命令 → (可自动执行的只读命令, 需人工确认的命令)。"""
    ok, blocked = [], []
    for c in commands:
        c = c.strip()
        if not c:
            continue
        if _ASSIST_READONLY_ALLOW.search(c) and not _ASSIST_DANGEROUS.search(c):
            ok.append(c)
        else:
            blocked.append(c)
    return ok, blocked


def build_assist_prompt(dev: dict, request: str, output: str,
                        done_commands: list[str], force_answer: bool = False,
                        failed_commands: list[str] | None = None) -> str:
    prof = netdev_service.profile_of(dev.get("vendor", ""))
    done = "\n".join(f"- {c}" for c in done_commands) if done_commands else "（无）"
    failed = "\n".join(f"- {c}" for c in failed_commands) if failed_commands else "（无）"
    failed_rule = (f"\n在设备上报过语法错误或命令不存在的命令（**禁止再次规划**，"
                   f"换用该厂商等价命令或直接放弃该项）：\n{failed}\n") if failed_commands else ""
    if force_answer:
        return (
            f"你是网络设备运维助手。设备：{prof.display}（{dev.get('host', '')}），"
            f"用户请求：{request}\n已执行过的命令：\n{done}\n{failed_rule}"
            f"终端最近回显（已脱敏，可能截断）：\n{output or '（空）'}\n\n"
            "自动执行轮次已用完，现在必须收尾作答：基于以上已采集回显直接给出结论。"
            "只输出 {\"action\": \"answer\", \"answer\": \"markdown\"}；"
            "已确认的信息给结论，缺失的部分在末尾用「待人工确认」短列出。"
            "禁止 execute、禁止寒暄。"
        )
    return (
        f"你是网络设备运维助手，在交互式控制台旁辅助工程师。"
        f"设备：{prof.display}（管理地址 {dev.get('host', '')}:{dev.get('port', 22)}，型号 {dev.get('model') or '未知'}）。\n"
        f"用户请求：{request}\n"
        f"本轮已执行过的命令：\n{done}\n{failed_rule}"
        f"终端最近回显（已脱敏，可能截断）：\n{output or '（空）'}\n\n"
        "决定下一步，只输出 JSON 之一：\n"
        '{"action": "execute", "commands": ["命令"], "note": "≤20字执行理由"}\n'
        '{"action": "answer", "answer": "markdown 结论/分析", "commands": ["可选建议命令"]}\n'
        "规则：\n"
        "1) 查看状态/日志/利用率/版本等需要设备数据 → execute：只读查询命令，"
        f"严格 {prof.display} CLI 语法（禁止混用其它厂商语法），接口名不含空格"
        "（如 Ten-GigabitEthernet1/0/25）；\n"
        "2) **精简与定向**：一次 execute 集中给齐所需查询（最多 4 条），优先带接口名/编号的定向查询"
        "（如 display interface Ten-GigabitEthernet1/0/25 而非全量接口列表），"
        "不得与已执行命令语义重复；\n"
        "3) 诊断类只读命令示例——H3C：transceiver diagnose interface X（光模块）、"
        "display packet-drop interface X（错误/丢包统计）；华为：display transceiver interface X；"
        "思科：show interfaces transceiver / show interfaces counters errors；\n"
        "4) 巡检/多处查看类请求：一次给齐命令清单尽快收集（最多 3 轮），收集齐后立即 answer；"
        "日志类查询优先带条数（display logbuffer size 20 / show logging | last 20）避免刷屏；\n"
        "4) 回显信息足够作答，或请求本身是解释/建议/排查思路 → answer：直接给结论；\n"
        "   日志/回显分析按「结论 → 关键发现（逐条，标注严重级别）→ 建议」组织，"
        "只归纳要点，不要逐条复述原始日志；\n"
        "5) commands 禁止任何修改/配置类命令（save/conf/undo/set/reset/reboot 等）；"
        "只规划确定存在于该厂商 CLI 的命令，无把握的不放；\n"
        "6) 直接输出结果：禁止寒暄、禁止复述请求、禁止「我将为您」类空话。"
    )


@router.post("/console/assist")
async def console_assist(payload: ConsoleAssistIn):
    """控制台 AI 助手（规划单步）：意图 → 自动执行只读命令（前端代发）或直接回答。

    安全边界：返回的 execute 命令经服务端只读白名单过滤；非只读命令降级为
    "人工确认"清单由前端插入终端不回车，绝不代发修改类命令。
    """
    dev = db.get_netdev_device(payload.device_id)
    if not dev:
        raise HTTPException(404, "设备不存在")
    request_text = payload.request.strip()
    if not request_text:
        raise HTTPException(400, "请输入运维请求，如：查看 CPU 利用率 / 分析最近的 20 条日志")
    llm = get_llm_config()
    if not (llm.get("api_key") and llm.get("base_url")):
        raise HTTPException(409, "未配置大模型（平台设置 → 大模型接入），AI 助手不可用")

    output = sanitize_terminal_output(payload.output)
    client = AsyncOpenAI(api_key=llm["api_key"], base_url=llm["base_url"], timeout=60)
    resp = await client.chat.completions.create(
        model=llm["model"], temperature=0.2, max_tokens=1600,
        messages=[{"role": "system", "content": "你是网络设备运维助手，只输出 JSON。"},
                  {"role": "user", "content": build_assist_prompt(
                      dev, request_text, output, payload.done_commands, payload.force_answer,
                      payload.failed_commands)}])
    text = (resp.choices[0].message.content or "").strip()

    def _fallback_answer() -> dict:
        # 模型未按 JSON 输出时整段当回答兜底（分析类请求不至于空手而归）
        return {"action": "answer", "answer": text or "（模型未返回有效内容，请重试）", "commands": []}

    stripped = re.sub(r"```(?:json)?", "", text).strip()
    try:
        m = re.search(r"\{.*\}", stripped, re.S)
        parsed = json.loads(m.group(0), strict=False) if m else None
    except Exception:   # noqa: BLE001
        parsed = None
    if not isinstance(parsed, dict) or "action" not in parsed:
        result = _fallback_answer()
    elif parsed.get("action") == "execute" and not payload.force_answer:
        cmds = [str(c).strip() for c in (parsed.get("commands") or []) if str(c).strip()][:4]
        exclude = ({c.strip() for c in payload.done_commands}
                   | {c.strip() for c in payload.failed_commands})
        ok, blocked = filter_readonly_commands([c for c in cmds if c not in exclude])
        if not ok and blocked:
            # 只给出了非只读命令 → 不代发，转为回答并附人工确认命令
            result = {"action": "answer",
                      "answer": f"该请求涉及非只读命令，需要你在终端人工执行：{blocked[0]}",
                      "commands": blocked[:4]}
        elif not ok:
            result = _fallback_answer()
        else:
            result = {"action": "execute", "commands": ok,
                      "note": str(parsed.get("note") or "").strip()[:60]}
    else:
        # answer；force_answer 下模型仍返回 execute 时降级为文本回答，保证收尾
        answer = str(parsed.get("answer") or "").strip()
        if not answer and payload.force_answer:
            answer = "自动执行轮次已用完。请查看上方已采集的回显继续人工分析，或换一个更具体的请求。"
        if not answer:
            result = _fallback_answer()
        else:
            # 建议命令按只读/人工拆分：只读命令前端点击直接执行，其余仅插入终端
            commands = [str(c).strip() for c in (parsed.get("commands") or []) if str(c).strip()][:4]
            readonly_cmds, manual_cmds = filter_readonly_commands(commands)
            result = {"action": "answer", "answer": answer,
                      "commands": readonly_cmds, "manual_commands": manual_cmds}

    db.audit("netdev.console.assist",
             {"args": {"device": dev.get("name", ""), "request": request_text[:120],
                       "action": result["action"], "output_chars": len(output)}},
             device_id=payload.device_id)
    return result
