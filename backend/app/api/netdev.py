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
