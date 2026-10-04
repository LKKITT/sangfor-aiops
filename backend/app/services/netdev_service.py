"""网络设备管理服务：常见厂家交换机/路由器的 SSH 远程管理与批量并行命令执行。

- 厂商档案：分页命令 / 版本命令 / 提示符特征（华为 VRP、H3C Comware、思科 IOS、
  锐捷 RGSOS、中兴 ZXR10，其他厂家按通用提示符处理）；
- SSH 执行：asyncssh 建立交互式会话（vt100），逐条下发命令，按提示符或空闲窗口
  判定输出结束；可选 enable/super 口令提权；
- 批量执行：asyncio.gather + 信号量限流（默认并发 10），每台设备独立超时，
  进度实时落库（netdev_task_items），任务级状态机 running/done/failed。

认证信息（密码）仅存本机 SQLite，与既有 devices 表口径一致；接口出参不回传密码。
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass

from app import db

log = logging.getLogger("sangfor-agent.netdev")

try:
    import asyncssh
    SSH_AVAILABLE = True
except ImportError:   # 未安装 asyncssh 时设备管理功能不可用（其余功能不受影响）
    SSH_AVAILABLE = False

BATCH_CONCURRENCY = 20        # 批量执行并发上限（同批同时在线 SSH 会话数）
IDLE_WINDOW = 1.2             # 输出空闲窗口秒数：无新数据即认为本条命令输出结束
LOGIN_IDLE_WINDOW = 1.2       # 登录横幅/提示符的空闲窗口
POLL_INTERVAL = 0.03          # 读取轮询粒度（提示符响应延迟的下限）


@dataclass
class VendorProfile:
    vendor: str
    display: str
    paging_cmd: str            # 关闭分页显示
    version_cmd: str           # 版本查看（连接测试用）
    prompt_re: str             # 提示符特征（行尾）
    enable_cmd: str = "enable"       # 提权命令（思科系 enable / 华为系 super）
    super_probe: str = ""            # 提权口令提示（用于识别口令输入态）


VENDOR_PROFILES: dict[str, VendorProfile] = {
    "huawei": VendorProfile("huawei", "华为 VRP", "screen-length 0 temporary",
                            "display version", r"[<\[][\w\-.:/ -]+[>\]]\s*$",
                            enable_cmd="super"),
    "h3c": VendorProfile("h3c", "H3C Comware", "screen-length disable",
                         "display version", r"[<\[][\w\-.:/ -]+[>\]]\s*$",
                         enable_cmd="super"),
    "cisco": VendorProfile("cisco", "思科 IOS", "terminal length 0",
                           "show version", r"[\w\-.:/ -]+[#>]\s*$"),
    "ruijie": VendorProfile("ruijie", "锐捷 RGSOS", "terminal length 0",
                            "show version", r"[\w\-.:/ -]+[#>]\s*$"),
    "zte": VendorProfile("zte", "中兴 ZXR10", "screen-length 0",
                         "show version", r"[\w\-.:/ -]+[#>]\s*$"),
    "juniper": VendorProfile("juniper", "Juniper JunOS", "set cli screen-length 0",
                             "show version", r"[\w@\-.:/ -]+[>#]\s*$"),
    "aruba": VendorProfile("aruba", "HPE Aruba", "no paging",
                           "show version", r"[\w\-.:/ -]+[#>]\s*$"),
    "dell": VendorProfile("dell", "Dell Networking", "terminal length 0",
                          "show version", r"[\w\-.:/ -]+[#>]\s*$"),
    "tplink": VendorProfile("tplink", "TP-Link", "terminal length 0",
                            "show version", r"[\w\-.:/ -]+[#>]\s*$"),
    "mikrotik": VendorProfile("mikrotik", "MikroTik RouterOS", "",
                              "/system resource print", r"[\w@\-.:/ \[\]]+>\s*$"),
    "nokia": VendorProfile("nokia", "Nokia SR OS", "environment no more",
                           "show version", r"[\w\-.:/ -]+[#>]\s*$"),
    "other": VendorProfile("other", "通用", "terminal length 0",
                           "show version", r"[#>]\s*$"),
}

PROMPT_FALLBACK = re.compile(r"(?:[<\[][\w\-.:/ -]+[>\]]|[\w\-.:/ -]+[#>])\s*$")

# 老旧网络设备（如 H3C 老固件）仅支持 CBC/3DES 等传统算法与 SHA1 DH 交换，
# asyncssh 默认提案不含这些——用 "+" 前缀在默认列表基础上**追加**传统算法
# （与 AF 适配器放宽 SSL SECLEVEL 同理，面向存量设备的兼容取舍）
LEGACY_ENCRYPTION_ALGS = "+aes128-cbc,aes256-cbc,3des-cbc"
LEGACY_KEX_ALGS = "+diffie-hellman-group14-sha1,diffie-hellman-group1-sha1"
LEGACY_HOST_KEY_ALGS = "+ssh-rsa"


def ssh_connect_kwargs(device: dict) -> dict:
    """asyncssh.connect 统一连接参数（批量执行与 WS 控制台共用）。"""
    return {
        "host": device["host"],
        "port": int(device.get("port") or 22),
        "username": device.get("username", ""),
        "password": device.get("password", ""),
        "known_hosts": None,
        "encryption_algs": LEGACY_ENCRYPTION_ALGS,
        "kex_algs": LEGACY_KEX_ALGS,
        "server_host_key_algs": LEGACY_HOST_KEY_ALGS,
    }


def profile_of(vendor: str) -> VendorProfile:
    return VENDOR_PROFILES.get((vendor or "").strip().lower(), VENDOR_PROFILES["other"])


def mask_output(output: str) -> str:
    """输出脱敏：命令回显里的口令行替换（enable/super 提权场景）。"""
    return re.sub(r"(?i)(password|口令)\s*[:：]?\s*\S+", r"\1 ***", output)


async def read_until_idle(stream, idle_window: float, hard_deadline: float,
                          prompt_re: re.Pattern | None = None,
                          min_len: int = 0) -> str:
    """持续读取 stdout 直到：提示符匹配 / 空闲窗口无新数据 / 硬超时。

    三个条件按最快命中者返回；idle_window 自最后收到数据起算，
    连接后始终无输出的场景同样按 idle_window 提前返回（如无横幅直出提示符）。
    """
    buf: list[str] = []
    state = {"last_data": None}

    async def _reader():
        while True:
            data = await stream.read(4096)
            if not data:
                return
            buf.append(data)
            state["last_data"] = asyncio.get_running_loop().time()

    reader = asyncio.ensure_future(_reader())
    started = asyncio.get_running_loop().time()
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            text = "".join(buf)
            now = asyncio.get_running_loop().time()
            if prompt_re and text and len(text) >= max(min_len, 1):
                tail = text.rstrip()[-200:]
                if prompt_re.search(tail):
                    return text
            if state["last_data"] is not None:
                if now - state["last_data"] >= idle_window:
                    return text
            elif now - started >= idle_window:
                # 尚未收到任何输出：idle_window 内无响应即返回（无横幅/静默设备）
                return text
            if now >= hard_deadline:
                return text
    finally:
        reader.cancel()


class SSHResult(dict):
    pass


# ---------------- SSH 连接池（跨任务复用，省去重复握手/认证） ----------------

CONN_POOL_TTL = 120.0         # 空闲连接保留时长（秒），过期由清理任务关闭
CONN_POOL_MAX = 24            # 池内空闲连接上限（与批量并发同级）
_CONN_POOL: dict[str, tuple] = {}      # key -> (conn, last_used_ts)
_pool_cleaner_task = None


def _pool_key(device: dict) -> str:
    return f"{device['host']}:{device.get('port') or 22}:{device.get('username', '')}"


def _ensure_pool_cleaner() -> None:
    global _pool_cleaner_task
    if _pool_cleaner_task and not _pool_cleaner_task.done():
        return

    async def _cleaner():
        while _CONN_POOL:
            await asyncio.sleep(20)
            now = asyncio.get_running_loop().time()
            for key in [k for k, (_, ts) in _CONN_POOL.items() if now - ts > CONN_POOL_TTL]:
                conn, _ = _CONN_POOL.pop(key)
                try:
                    conn.close()
                except Exception:   # noqa: BLE001
                    pass

    _pool_cleaner_task = asyncio.get_running_loop().create_task(_cleaner())


async def _get_or_connect(device: dict, timeout: float):
    """优先复用池内空闲连接；无可用连接时新建。返回 (conn, reused)。"""
    conn = _CONN_POOL.pop(_pool_key(device), None)
    if conn is not None:
        try:
            if not conn[0].is_closed():
                return conn[0], True
        except Exception:   # noqa: BLE001
            pass
        try:
            conn[0].close()
        except Exception:   # noqa: BLE001
            pass
    fresh = await asyncio.wait_for(
        asyncssh.connect(**ssh_connect_kwargs(device)),
        timeout=min(10.0, timeout))
    return fresh, False


def _release_conn(device: dict, conn) -> None:
    """归还连接进池；池满或连接异常则直接关闭。"""
    try:
        if conn.is_closed():
            return
    except Exception:   # noqa: BLE001
        return
    key = _pool_key(device)
    if key not in _CONN_POOL and len(_CONN_POOL) < CONN_POOL_MAX:
        _CONN_POOL[key] = (conn, asyncio.get_running_loop().time())
        _ensure_pool_cleaner()
    else:
        try:
            conn.close()
        except Exception:   # noqa: BLE001
            pass


async def run_commands(device: dict, commands: list[str], timeout: float = 30,
                       idle_window: float = IDLE_WINDOW,
                       reuse_conn: bool = True) -> SSHResult:
    """在单台设备上顺序执行命令并聚合输出。任何一步失败返回 ok=False + error。

    reuse_conn=True 时连接执行完归还进池（默认），供同一设备连续任务复用。
    """
    if not SSH_AVAILABLE:
        return SSHResult(ok=False, error="asyncssh 未安装（pip install asyncssh），SSH 功能不可用")
    prof = profile_of(device.get("vendor", ""))
    host = device["host"]
    started = time.monotonic()
    conn = None
    pooled = False
    ok_done = False   # 仅成功执行完毕的连接才归还进池（异常路径的会话状态不可信）
    try:
        if reuse_conn:
            conn, pooled = await _get_or_connect(device, timeout)
        else:
            conn = await asyncio.wait_for(
                asyncssh.connect(**ssh_connect_kwargs(device)),
                timeout=min(10.0, timeout))
        async with conn.create_process(term_type="vt100", term_size=(220, 60)) as proc:
            # 登录横幅/首提示符（提示符命中即返回，无横幅按空闲窗早退）
            await read_until_idle(proc.stdout, LOGIN_IDLE_WINDOW,
                                  time.monotonic() + timeout, PROMPT_FALLBACK)
            # 可选提权（enable/super）
            enable_pwd = device.get("enable_password", "")
            if enable_pwd:
                proc.stdin.write(prof.enable_cmd + "\n")
                await read_until_idle(proc.stdout, idle_window,
                                             time.monotonic() + 10,
                                             re.compile(r"(?i)(password|口令)\s*[:：]?\s*$"), 1)
                proc.stdin.write(enable_pwd + "\n")
                await read_until_idle(proc.stdout, idle_window, time.monotonic() + 10,
                                      PROMPT_FALLBACK, 1)
            # 关闭分页（部分厂家无分页概念如 MikroTik，paging_cmd 为空时跳过）
            if prof.paging_cmd:
                proc.stdin.write(prof.paging_cmd + "\n")
                await read_until_idle(proc.stdout, idle_window, time.monotonic() + 10,
                                      PROMPT_FALLBACK, 1)
            # 逐条执行
            outputs: list[str] = []
            for cmd in commands:
                cmd = (cmd or "").strip()
                if not cmd:
                    continue
                proc.stdin.write(cmd + "\n")
                out = await read_until_idle(proc.stdout, idle_window,
                                            time.monotonic() + timeout,
                                            PROMPT_FALLBACK, min_len=1)
                outputs.append(f"{device['name']}@{host}> {cmd}\n{out.rstrip()}")
            db.update_netdev_last_ok(device["id"])
            ok_done = True
            return SSHResult(ok=True, output="\n\n".join(outputs) or "（无输出）",
                             duration=round(time.monotonic() - started, 2))
    except asyncio.TimeoutError:
        return SSHResult(ok=False, error=f"连接或执行超时（>{int(timeout)}s）",
                         duration=round(time.monotonic() - started, 2))
    except asyncssh.misc.PermissionDenied:
        return SSHResult(ok=False, error="SSH 认证失败：用户名或口令错误",
                         duration=round(time.monotonic() - started, 2))
    except (OSError, asyncssh.Error) as e:
        return SSHResult(ok=False, error=f"SSH 连接失败：{e}",
                         duration=round(time.monotonic() - started, 2))
    except Exception as e:   # noqa: BLE001 —— 未预期异常（参数/环境等）转友好失败，不逃逸
        log.warning("SSH 执行未预期异常 host=%s: %s", host, e, exc_info=True)
        return SSHResult(ok=False, error=f"SSH 执行异常：{e}",
                         duration=round(time.monotonic() - started, 2))
    finally:
        if conn is not None:
            if reuse_conn and ok_done:
                _release_conn(device, conn)
            else:
                try:
                    conn.close()
                except Exception:   # noqa: BLE001
                    pass


# ---------------- 批量执行编排（并行 + 进度落库） ----------------

async def _run_one_task_item(task_id: str, device: dict, commands: list[str],
                             timeout: float, semaphore: asyncio.Semaphore) -> None:
    item_id = db.new_id("ndi_")
    db.save_netdev_task_item({"id": item_id, "task_id": task_id, "device_id": device["id"],
                              "device_name": device["name"], "status": "running"})
    async with semaphore:
        result = await run_commands(device, commands, timeout=timeout)
    db.save_netdev_task_item({
        "id": item_id, "task_id": task_id, "device_id": device["id"],
        "device_name": device["name"],
        "status": "ok" if result["ok"] else "failed",
        "output": mask_output(result.get("output", "")) if result["ok"] else "",
        "error": result.get("error", ""),
        "duration": result.get("duration", 0)})


async def _run_batch(task_id: str, devices: list[dict], commands: list[str],
                     timeout: float) -> None:
    semaphore = asyncio.Semaphore(BATCH_CONCURRENCY)
    results = await asyncio.gather(
        *[_run_one_task_item(task_id, d, commands, timeout, semaphore) for d in devices],
        return_exceptions=True)
    for d, r in zip(devices, results, strict=True):
        if isinstance(r, BaseException):   # 单台编排异常不拖垮任务
            log.warning("批量执行单台编排异常 device=%s: %s", d["id"], r, exc_info=True)
            db.save_netdev_task_item({"task_id": task_id, "device_id": d["id"],
                                      "device_name": d["name"], "status": "failed",
                                      "error": f"执行异常：{r}"})
    ok = sum(1 for i in (db.get_netdev_task(task_id) or {}).get("items", [])
             if i["status"] == "ok")
    db.finish_netdev_task(task_id, "done" if ok else "failed")


def start_batch_task(name: str, devices: list[dict], commands: list[str],
                     timeout: float = 30) -> str:
    """创建任务记录并启动后台批量执行，返回 task_id（前端轮询进度）。"""
    task = db.create_netdev_task({"name": name, "commands": commands,
                                  "device_ids": [d["id"] for d in devices],
                                  "timeout": timeout})
    asyncio.get_running_loop().create_task(
        _run_batch(task["id"], devices, commands, timeout))
    return task["id"]
