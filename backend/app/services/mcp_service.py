"""MCP（Model Context Protocol）服务管理：配置存储、会话管理、工具桥接与资源发现。

- 配置存 DB app_settings（key=mcp_servers，JSON 数组），保存即生效（会话热重建）；
- 会话用 AsyncExitStack 常驻（stdio 子进程 / streamable-http），懒连接 + 失败自动重连；
- 已启用服务的工具桥接为 Agent 工具（名称前缀 mcp_<server>_，schema 原样透传），
  编排器 _tool_scope 追加注入，LLM 直接以 function-calling 调用；
- 资源发现：代理 MCP 官方注册表（registry.modelcontextprotocol.io）搜索；
  支持粘贴 Claude Desktop / Cursor 格式 mcpServers JSON 一键导入。
"""
import asyncio
import json
import logging
import re
from contextlib import AsyncExitStack

from app import db
from app.agent.tools import Tool

mcp_log = logging.getLogger("sangfor-agent.mcp")

MCP_CONFIG_KEY = "mcp_servers"
MCP_SDK_HINT = "未安装 mcp Python SDK（backend: pip install mcp），MCP 功能不可用"


# ---------------- 配置存取 ----------------

def get_servers() -> list[dict]:
    raw = db.get_setting(MCP_CONFIG_KEY, "[]")
    try:
        servers = json.loads(raw)
        return servers if isinstance(servers, list) else []
    except ValueError:
        return []


def save_servers(servers: list[dict]) -> None:
    db.set_setting(MCP_CONFIG_KEY, json.dumps(servers, ensure_ascii=False, indent=2))
    _reset_sessions()


def upsert_server(server: dict) -> dict:
    """新增/更新单个服务配置（按 id），返回整理后的记录。"""
    rec = _normalize(server)
    servers = get_servers()
    for i, s in enumerate(servers):
        if s.get("id") == rec["id"]:
            servers[i] = rec
            break
    else:
        servers.append(rec)
    save_servers(servers)
    return rec


def delete_server(server_id: str) -> bool:
    servers = get_servers()
    left = [s for s in servers if s.get("id") != server_id]
    if len(left) == len(servers):
        return False
    save_servers(left)
    return True


def _normalize(server: dict) -> dict:
    sid = str(server.get("id") or "").strip() or _slug(server.get("name") or "server") + "_" + db.new_id()[:6]
    transport = "http" if server.get("transport") == "http" else "stdio"
    rec = {"id": sid, "name": str(server.get("name") or sid).strip(),
           "transport": transport, "enabled": bool(server.get("enabled", False))}
    if transport == "stdio":
        rec["command"] = str(server.get("command") or "").strip()
        args = server.get("args") or []
        rec["args"] = [str(a) for a in args] if isinstance(args, list) else []
        env = server.get("env") or {}
        rec["env"] = {str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {}
        if not rec["command"]:
            raise ValueError("stdio 传输必须填写启动命令（command）")
    else:
        rec["url"] = str(server.get("url") or "").strip()
        headers = server.get("headers") or {}
        rec["headers"] = {str(k): str(v) for k, v in headers.items()} if isinstance(headers, dict) else {}
        if not rec["url"].startswith(("http://", "https://")):
            raise ValueError("http 传输必须填写合法 URL")
    return rec


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", s)[:24] or "server"


# ---------------- 会话管理（每服务驻留后台任务：MCP 上下文 enter/exit 必须同任务） ----------------

_sessions: dict[str, dict] = {}   # server_id → {"session": ClientSession, "task": Task}
_locks: dict[str, asyncio.Lock] = {}
_locks_lock = asyncio.Lock()


async def _open_and_park(server: dict, ready: asyncio.Future) -> None:
    """专属任务内建立会话并驻留：进入全部上下文后挂起，直到被取消
    （取消发生在同一任务内，满足 anyio 取消作用域约束）。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    mcp_log.info("MCP[%s]: 启动子进程 %s %s (CWD=%s)", server["name"],
                 server.get("command"), " ".join(server.get("args") or []),
                 __import__("os").getcwd())
    async with AsyncExitStack() as stack:
        if server["transport"] == "stdio":
            params = StdioServerParameters(command=server["command"],
                                           args=server.get("args") or [],
                                           env=server.get("env") or None)
            read, write = await stack.enter_async_context(stdio_client(params))
            mcp_log.info("MCP[%s]: stdio 通道已建立", server["name"])
        else:
            read, write, _ = await stack.enter_async_context(
                streamablehttp_client(server["url"], headers=server.get("headers") or None))
        session = await stack.enter_async_context(ClientSession(read, write))
        mcp_log.info("MCP[%s]: ClientSession 已进入，开始 initialize", server["name"])
        await session.initialize()
        mcp_log.info("MCP[%s]: initialize 完成", server["name"])
        _sessions[server["id"]] = {"session": session, "task": asyncio.current_task()}
        if not ready.done():
            ready.set_result(session)
        await asyncio.Event().wait()   # 驻留


def _reset_sessions() -> None:
    """配置变更/停机：取消各驻留任务（上下文在其自身任务内干净退出）。"""
    for sid, item in list(_sessions.items()):
        task = item.get("task")
        if task and not task.done():
            task.cancel()
        _sessions.pop(sid, None)
    _locks.clear()


async def _get_session(server: dict):
    """取（或建立）该服务的 MCP 会话：懒连接 + 30s 建立超时 + 失败自清理。"""
    try:
        import importlib.util
        if importlib.util.find_spec("mcp") is None:
            raise RuntimeError(MCP_SDK_HINT)
    except RuntimeError:
        raise

    sid = server["id"]
    existing = _sessions.get(sid)
    if existing and existing.get("task") and not existing["task"].done():
        return existing["session"]
    async with _locks_lock:
        lock = _locks.setdefault(sid, asyncio.Lock())
    async with lock:
        existing = _sessions.get(sid)
        if existing and existing.get("task") and not existing["task"].done():
            return existing["session"]
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(_open_and_park(server, ready))
        try:
            session = await asyncio.wait_for(ready, timeout=30)
        except BaseException:
            task.cancel()
            try:
                await task
            except Exception:   # noqa: BLE001
                pass
            raise
        return session


def shutdown() -> None:
    """应用停机：关闭全部 MCP 会话（由各驻留任务在自身上下文内退出）。"""
    _reset_sessions()


# ---------------- Agent 工具桥接 ----------------

_tools_cache: list[Tool] | None = None


def invalidate_tools_cache() -> None:
    global _tools_cache
    _tools_cache = None
    _reset_sessions()


async def agent_tools(server_ids=None) -> list[Tool]:
    """已启用 MCP 服务的工具桥接为 Agent Tool（全部成功才缓存；失败不缓存、下次重试）。

    server_ids=None 表示全部已启用；传 id 列表则只返回指定服务（对话级选用）。"""
    global _tools_cache
    if _tools_cache is not None:
        tools = _tools_cache
        if server_ids is None:
            return tools
        return [t for t in tools if getattr(t, "_mcp_server_id", "") in set(server_ids)]
    tools: list[Tool] = []
    failed: list[str] = []
    for server in get_servers():
        if not server.get("enabled"):
            continue
        server_id = server["id"]
        try:
            session = await _get_session(server)
            listing = await session.list_tools()
        except Exception as e:   # noqa: BLE001 —— 单服务失联不拖垮其它
            logging.getLogger("sangfor-agent").warning(
                "MCP 服务「%s」连接失败，本次以不可用占位返回（下次调用自动重试）: %s",
                server["name"], e)
            failed.append(server["name"])
            unavail = Tool(name=f"mcp_{_slug(server['name'])}_unavailable",
                           description=f"MCP 服务「{server['name']}」当前不可用：{e}",
                           parameters={"type": "object", "properties": {}},
                           handler=_mk_unavailable(server["name"]),
                           device_type=None, needs_device=False, batch_devices=False)
            unavail._mcp_server_id = server_id
            tools.append(unavail)
            continue
        for t in listing.tools:
            bridged = _bridge_tool(server, t)
            bridged._mcp_server_id = server_id   # 对话级选用过滤用
            tools.append(bridged)
    if not failed:   # 仅全部成功才缓存——失败不缓存，下次调用自动重试
        _tools_cache = tools
    return tools


def _mk_unavailable(server_name: str):
    async def _h(client, args, device):
        return {"error": f"MCP 服务「{server_name}」当前不可用，请到「平台设置 → MCP 配置」检查"}
    return _h


def _bridge_tool(server: dict, t) -> Tool:
    fname = _func_name(server["name"], t.name)
    desc = (t.description or t.name).strip()
    if len(desc) > 300:
        desc = desc[:300] + "…"
    return Tool(
        name=fname,
        description=f"[MCP·{server['name']}] {desc}",
        parameters=t.inputSchema if isinstance(t.inputSchema, dict)
                   else {"type": "object", "properties": {}},
        handler=_mk_handler(server, t.name),
        device_type=None, needs_device=False, batch_devices=False)


def _func_name(server_name: str, tool_name: str) -> str:
    n = f"mcp_{_slug(server_name)}_{_slug(tool_name)}"
    return n[:64]


def _mk_handler(server: dict, tool_name: str):
    async def _h(client, args, device):
        session = await _get_session(server)
        result = await session.call_tool(tool_name, arguments=args or {})
        return _result_to_dict(result)
    return _h


def _result_to_dict(result) -> dict:
    """MCP CallToolResult → 编排器可序列化 dict（content 各段拼接 + 结构化数据）。"""
    texts, structured = [], None
    for c in getattr(result, "content", None) or []:
        kind = getattr(c, "type", "")
        if kind == "text":
            texts.append(getattr(c, "text", ""))
        elif kind == "image":
            texts.append("[图片输出已省略]")
        elif kind == "resource":
            texts.append(f"[资源] {getattr(c, 'uri', '')}")
    s = getattr(result, "structuredContent", None)
    if s is not None:
        structured = s
    out = {"ok": not getattr(result, "isError", False), "text": "\n".join(texts)[:TOOL_TEXT_CAP]}
    if structured is not None:
        out["data"] = structured
    if getattr(result, "isError", False):
        out["error"] = out["text"][:300] or "MCP 工具执行报错"
    return out


TOOL_TEXT_CAP = 20000


# ---------------- 资源发现：官方注册表搜索 / Claude JSON 导入 ----------------

REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0/servers"


async def registry_search(keyword: str, limit: int = 12) -> dict:
    """代理搜索 MCP 官方注册表，归一为可安装项（http 远端直装 / stdio 包给启动命令）。"""
    import httpx
    params = {"search": keyword or "", "limit": str(min(max(limit, 1), 30))}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(REGISTRY_URL, params=params)
        r.raise_for_status()
        data = r.json()
    out = []
    for item in (data.get("servers") or [])[:limit]:
        srv = item.get("server") or {}
        name = srv.get("name") or ""
        remotes = [{"url": rm.get("url", ""), "type": rm.get("type", "")}
                   for rm in (srv.get("remotes") or []) if rm.get("url")]
        pkgs = []
        for pk in srv.get("packages") or []:
            runtime = pk.get("registry_type", "").lower()
            identifier = pk.get("identifier") or pk.get("name") or ""
            if not identifier:
                continue
            if runtime == "npm":
                pkgs.append({"runtime": "npx", "command": "npx",
                             "args": ["-y", identifier] + (pk.get("package_arguments") or [])})
            elif runtime in ("pypi", "oci"):
                pkgs.append({"runtime": "uvx", "command": "uvx",
                             "args": [identifier] + (pk.get("package_arguments") or [])})
        out.append({"registry_name": name, "display": name.split("/")[-1],
                    "description": (srv.get("description") or "")[:220],
                    "remotes": remotes, "packages": pkgs,
                    "status": item.get("status", "")})
    return {"total": len(out), "servers": out}


def install_from_registry(item: dict, enabled: bool = True) -> dict:
    """注册表条目 → 服务配置（优先 http 远端；否则取第一个 stdio 包预设）。"""
    if item.get("remotes"):
        remote = item["remotes"][0]
        return upsert_server({"name": item.get("display") or item.get("registry_name", "mcp"),
                              "transport": "http", "url": remote["url"], "enabled": enabled})
    for pk in item.get("packages") or []:
        return upsert_server({"name": item.get("display") or item.get("registry_name", "mcp"),
                              "transport": "stdio", "command": pk["command"],
                              "args": pk.get("args") or [], "enabled": enabled})
    raise ValueError("该注册表条目既无远程端点也无安装包信息，请手动配置")


def import_claude_json(text: str, enabled: bool = False) -> dict:
    """解析 Claude Desktop / Cursor 格式的 mcpServers JSON，批量导入（默认停用，逐个开启）。"""
    data = json.loads(text)
    servers = data.get("mcpServers") if isinstance(data, dict) else data
    if not isinstance(servers, dict) or not servers:
        raise ValueError("未识别到 mcpServers 配置（应形如 {\"mcpServers\": {名称: {command/args 或 url}}}}）")
    imported, errors = [], []
    for name, cfg in servers.items():
        try:
            rec = {"name": name, "enabled": enabled}
            if isinstance(cfg, dict) and cfg.get("url"):
                rec.update(transport="http", url=cfg["url"], headers=cfg.get("headers") or {})
            elif isinstance(cfg, dict) and cfg.get("command"):
                rec.update(transport="stdio", command=cfg["command"],
                           args=cfg.get("args") or [], env=cfg.get("env") or {})
            else:
                raise ValueError("缺少 command 或 url")
            imported.append(upsert_server(rec))
        except Exception as e:   # noqa: BLE001 —— 单条失败不影响其余
            errors.append(f"{name}: {e}")
    return {"imported": len(imported), "servers": imported, "errors": errors}
