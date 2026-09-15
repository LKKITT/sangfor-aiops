"""SQLite 数据访问层：设备、备份、待确认动作、审计日志、会话消息、更新缓存。

线程安全：thread-local 连接，WAL 模式；所有写入为轻量短事务。
"""
import json
import re
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from app.config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'af',            -- af / ac（可扩展）
    mode TEXT NOT NULL DEFAULT 'simulator',     -- simulator / real
    base_url TEXT NOT NULL,
    username TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL DEFAULT '',
    readonly INTEGER NOT NULL DEFAULT 0,
    settings_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    label TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'manual',        -- manual / scheduled / pre_change
    sw_version TEXT NOT NULL DEFAULT '',
    snapshot_json TEXT NOT NULL,
    file_path TEXT NOT NULL DEFAULT '',
    file_sha256 TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'user'
);
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY,
    conv_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    args_json TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',     -- pending / approved / rejected / executed
    result_json TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    conv_id TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'agent',
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    result TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '新对话',
    device_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conv_id TEXT NOT NULL,
    role TEXT NOT NULL,                          -- user / assistant / tool / system
    content_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS update_cache (
    id TEXT PRIMARY KEY,
    product TEXT NOT NULL,
    kind TEXT NOT NULL,                          -- release_notes / advisories / soft_list
    payload_json TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'fact',       -- fact / preference / action_history / device_context
    content TEXT NOT NULL,
    source_conv_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conv_summaries (
    id TEXT PRIMARY KEY,
    conv_id TEXT NOT NULL UNIQUE,
    summary TEXT NOT NULL DEFAULT '',
    device_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_entries (
    id TEXT PRIMARY KEY,
    conv_id TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '其他',
    summary TEXT NOT NULL DEFAULT '',
    content_md TEXT NOT NULL DEFAULT '',
    key_points_json TEXT NOT NULL DEFAULT '[]',
    references_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    product TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_reflections (
    id TEXT PRIMARY KEY,
    content_md TEXT NOT NULL,
    stats_json TEXT NOT NULL DEFAULT '{}',
    period TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_dismissed (
    conv_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_bindings (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,                       -- 渠道标识：wecom / 预留 feishu、qq 等
    sender_id TEXT NOT NULL,                     -- 渠道内发送方唯一标识（企微 userid 等）
    conv_id TEXT NOT NULL DEFAULT '',            -- 绑定的会话（首条消息自动创建）
    device_id TEXT NOT NULL DEFAULT '',          -- 当前操作设备（可通过"切换设备"指令变更）
    last_active_at TEXT NOT NULL DEFAULT '',     -- 最近一次对话时间（会话超时自动新开判断）
    updated_at TEXT NOT NULL,
    UNIQUE(channel, sender_id)
);
"""


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(settings.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)
        # 存量库迁移：conversations 补 device_id 列（用于按设备恢复最近会话）
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)")]
        if "device_id" not in cols:
            conn.execute("ALTER TABLE conversations ADD COLUMN device_id TEXT NOT NULL DEFAULT ''")
        kb_cols = [r["name"] for r in conn.execute("PRAGMA table_info(kb_entries)")]
        if "aliases_json" not in kb_cols:
            conn.execute("ALTER TABLE kb_entries ADD COLUMN aliases_json TEXT NOT NULL DEFAULT '[]'")
        chn_cols = [r["name"] for r in conn.execute("PRAGMA table_info(channel_bindings)")]
        if chn_cols and "last_active_at" not in chn_cols:
            conn.execute("ALTER TABLE channel_bindings ADD COLUMN last_active_at TEXT NOT NULL DEFAULT ''")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


# ---------------- devices ----------------

def list_devices() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM devices ORDER BY created_at").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_device(device_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
    return _row_to_dict(row) if row else None


def upsert_device(device: dict) -> dict:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO devices (id,name,type,mode,base_url,username,password,readonly,settings_json,created_at)
               VALUES (:id,:name,:type,:mode,:base_url,:username,:password,:readonly,:settings_json,:created_at)
               ON CONFLICT(id) DO UPDATE SET name=:name, type=:type, mode=:mode, base_url=:base_url,
                 username=:username, password=:password, readonly=:readonly, settings_json=:settings_json""",
            device,
        )
    return get_device(device["id"])


def delete_device(device_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM devices WHERE id=?", (device_id,))


# ---------------- backups ----------------

def create_backup(rec: dict) -> dict:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO backups (id,device_id,label,kind,sw_version,snapshot_json,file_path,file_sha256,created_at,created_by)
               VALUES (:id,:device_id,:label,:kind,:sw_version,:snapshot_json,:file_path,:file_sha256,:created_at,:created_by)""",
            rec,
        )
    return get_backup(rec["id"])


def get_backup(backup_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_backups(device_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id,device_id,label,kind,sw_version,file_path,file_sha256,created_at,created_by,"
            "length(snapshot_json) AS snapshot_size FROM backups WHERE device_id=? ORDER BY created_at DESC",
            (device_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_backup(backup_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM backups WHERE id=?", (backup_id,))


# ---------------- pending actions（变更确认流） ----------------

def create_pending_action(rec: dict) -> dict:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO pending_actions (id,conv_id,tool_name,args_json,summary,status,created_at)"
            " VALUES (:id,:conv_id,:tool_name,:args_json,:summary,:status,:created_at)",
            rec,
        )
    return get_pending_action(rec["id"])


def get_pending_action(action_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM pending_actions WHERE id=?", (action_id,)).fetchone()
    return _row_to_dict(row) if row else None


def update_pending_action(action_id: str, **fields: Any) -> None:
    if not fields:
        return
    sets = ",".join(f"{k}=:{k}" for k in fields)
    fields = dict(fields, id=action_id)
    with _connect() as conn:
        conn.execute(f"UPDATE pending_actions SET {sets} WHERE id=:id", fields)


def get_pending_action_by_conv(conv_id: str) -> Optional[dict]:
    """查找对话中待确认的变更动作（按创建时间取最新一条）。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM pending_actions WHERE conv_id=? AND status='pending' ORDER BY created_at DESC LIMIT 1",
            (conv_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


# ---------------- audit ----------------

def audit(action: str, detail: dict | None = None, conv_id: str = "", device_id: str = "",
          actor: str = "agent", result: str = "ok") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_logs (ts,conv_id,device_id,actor,action,detail_json,result) VALUES (?,?,?,?,?,?,?)",
            (now(), conv_id, device_id, actor, action, json.dumps(detail or {}, ensure_ascii=False), result),
        )


def list_audit(limit: int = 200) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------- conversations / messages ----------------

def create_conversation(title: str = "新对话", device_id: str = "") -> dict:
    conv = {"id": new_id("conv_"), "title": title[:40], "device_id": device_id,
            "created_at": now(), "updated_at": now()}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO conversations (id,title,device_id,created_at,updated_at)"
            " VALUES (:id,:title,:device_id,:created_at,:updated_at)", conv)
    return conv


def get_conversation(conv_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return _row_to_dict(row) if row else None


def touch_conversation(conv_id: str, title: Optional[str] = None,
                       device_id: Optional[str] = None) -> None:
    with _connect() as conn:
        if title:
            conn.execute("UPDATE conversations SET updated_at=?, title=? WHERE id=? AND title='新对话'",
                         (now(), title[:40], conv_id))
        if device_id:
            conn.execute("UPDATE conversations SET updated_at=?, device_id=? WHERE id=?",
                         (now(), device_id, conv_id))
        else:
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id))


def list_conversations(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_conversations_paged(page: int = 1, page_size: int = 20, keyword: str = "",
                             device_id: str = "", start: str = "", end: str = "") -> dict:
    """分页会话列表（含消息数/最后一条用户消息/摘要/设备名），支持筛选。

    单条 SQL 聚合完成，替代旧的“逐会话全量拉消息”实现。
    """
    page, page_size = max(1, page), min(max(1, page_size), 100)
    where, args = "1=1", []
    if keyword:
        where += " AND (c.title LIKE ? OR c.id IN (SELECT conv_id FROM messages WHERE content_json LIKE ?))"
        args += [f"%{keyword}%", f"%{keyword}%"]
    if device_id:
        where += " AND c.device_id = ?"
        args.append(device_id)
    if start:
        where += " AND c.updated_at >= ?"
        args.append(start)
    if end:
        where += " AND c.updated_at <= ?"
        args.append(f"{end} 23:59:59")
    with _connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM conversations c WHERE {where}",
                             args).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT c.id, c.title, c.device_id, c.created_at, c.updated_at,
                  (SELECT COUNT(*) FROM messages m WHERE m.conv_id = c.id) AS msg_count,
                  (SELECT content_json FROM messages m
                    WHERE m.conv_id = c.id AND m.role = 'user' ORDER BY id DESC LIMIT 1) AS last_user_json,
                  (SELECT s.summary FROM conv_summaries s WHERE s.conv_id = c.id) AS summary,
                  d.name AS device_name
                FROM conversations c LEFT JOIN devices d ON d.id = c.device_id
                WHERE {where} ORDER BY c.updated_at DESC LIMIT ? OFFSET ?""",
            args + [page_size, (page - 1) * page_size]).fetchall()
    items = []
    for r in rows:
        d = _row_to_dict(r)
        try:
            last_user = json.loads(d.pop("last_user_json") or "null")
        except (ValueError, TypeError):
            last_user = None
        d["last_message"] = (last_user or {}).get("text", "")[:100]
        items.append(d)
    return {"total": total, "page": page, "page_size": page_size, "items": items}


def add_message(conv_id: str, role: str, content: dict) -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO messages (conv_id,role,content_json,created_at) VALUES (?,?,?,?)",
                     (conv_id, role, json.dumps(content, ensure_ascii=False), now()))
    touch_conversation(conv_id)


def max_message_id(conv_id: str) -> int:
    """会话当前最大消息 id（新消息从该 id 之后开始），用于增量提取。"""
    with _connect() as conn:
        row = conn.execute("SELECT MAX(id) AS m FROM messages WHERE conv_id=?", (conv_id,)).fetchone()
    return (row["m"] or 0) if row else 0


def get_messages(conv_id: str, limit: Optional[int] = None,
                 since_id: Optional[int] = None) -> list[dict]:
    """会话消息（按 id 升序）。limit=只取最近 N 条；since_id=只取该 id 之后的新消息。"""
    with _connect() as conn:
        if since_id:
            if limit:
                rows = conn.execute(
                    "SELECT * FROM (SELECT * FROM messages WHERE conv_id=? AND id>? ORDER BY id DESC LIMIT ?)"
                    " ORDER BY id", (conv_id, since_id, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conv_id=? AND id>? ORDER BY id",
                    (conv_id, since_id)).fetchall()
        elif limit:
            rows = conn.execute(
                "SELECT * FROM (SELECT * FROM messages WHERE conv_id=? ORDER BY id DESC LIMIT ?)"
                " ORDER BY id", (conv_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id",
                                (conv_id,)).fetchall()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["content"] = json.loads(d.pop("content_json"))
        out.append(d)
    return out


def latest_conversation_by_device(device_id: str) -> Optional[dict]:
    """该设备最近一次会话（按 updated_at），用于对话历史恢复。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE device_id=? ORDER BY updated_at DESC LIMIT 1",
            (device_id,)).fetchone()
    return _row_to_dict(row) if row else None


# ---------------- update cache ----------------

def save_update_cache(product: str, kind: str, payload: Any, source: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO update_cache (id,product,kind,payload_json,source,fetched_at) VALUES (?,?,?,?,?,?)",
            (new_id("upd_"), product, kind, json.dumps(payload, ensure_ascii=False), source, now()),
        )


def get_update_cache(product: str, kind: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM update_cache WHERE product=? AND kind=? ORDER BY fetched_at DESC LIMIT 1",
            (product, kind),
        ).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    d["payload"] = json.loads(d.pop("payload_json"))
    return d


# ---------------- app settings（KV 配置，如支持平台 Cookie） ----------------

def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO app_settings (key,value,updated_at) VALUES (:key,:value,:updated_at)"
            " ON CONFLICT(key) DO UPDATE SET value=:value, updated_at=:updated_at",
            {"key": key, "value": value, "updated_at": now()},
        )


# ---------------- channel bindings（外部消息渠道的用户/会话/设备绑定） ----------------

def get_channel_binding(channel: str, sender_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM channel_bindings WHERE channel=? AND sender_id=?",
                           (channel, sender_id)).fetchone()
    return _row_to_dict(row) if row else None


def upsert_channel_binding(channel: str, sender_id: str,
                           conv_id: Optional[str] = None,
                           device_id: Optional[str] = None) -> dict:
    """创建或更新绑定；传 None 的字段保持原值。任何调用都刷新 last_active_at。"""
    with _connect() as conn:
        row = conn.execute("SELECT id,conv_id,device_id FROM channel_bindings"
                           " WHERE channel=? AND sender_id=?", (channel, sender_id)).fetchone()
        if row is None:
            rec = {"id": new_id("chn_"), "channel": channel, "sender_id": sender_id,
                   "conv_id": conv_id or "", "device_id": device_id or "",
                   "last_active_at": now(), "updated_at": now()}
            conn.execute(
                "INSERT INTO channel_bindings (id,channel,sender_id,conv_id,device_id,"
                "last_active_at,updated_at)"
                " VALUES (:id,:channel,:sender_id,:conv_id,:device_id,:last_active_at,:updated_at)",
                rec)
            return rec
        sets = {k: v for k, v in {"conv_id": conv_id, "device_id": device_id}.items()
                if v is not None}
        sets["last_active_at"] = now()
        assign = ",".join(f"{k}=:{k}" for k in sets)
        conn.execute(f"UPDATE channel_bindings SET {assign}, updated_at=:updated_at"
                     " WHERE channel=:channel AND sender_id=:sender_id",
                     {**sets, "updated_at": now(), "channel": channel, "sender_id": sender_id})
        row = conn.execute("SELECT id,conv_id,device_id,last_active_at FROM channel_bindings"
                           " WHERE channel=? AND sender_id=?", (channel, sender_id)).fetchone()
        return {"id": row["id"], "channel": channel, "sender_id": sender_id,
                "conv_id": row["conv_id"], "device_id": row["device_id"],
                "last_active_at": row["last_active_at"], "updated_at": now()}


# ---------------- backup file helpers ----------------

def backup_file_path(backup_id: str, suffix: str = ".conf") -> Path:
    p = settings.backup_dir / f"{backup_id}{suffix}"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ---------------- memory （短期会话摘要 + 长期记忆项） ----------------

def save_conv_summary(conv_id: str, summary: str, device_id: str = "") -> dict:
    rec = {"id": new_id("mem_"), "conv_id": conv_id, "summary": summary,
           "device_id": device_id, "created_at": now(), "updated_at": now()}
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO conv_summaries (id,conv_id,summary,device_id,created_at,updated_at)"
            " VALUES (:id,:conv_id,:summary,:device_id,:created_at,:updated_at)", rec)
    return rec


def get_conv_summary(conv_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM conv_summaries WHERE conv_id=?", (conv_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_conv_summaries(device_id: str, limit: int = 10) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT cs.* FROM conv_summaries cs JOIN conversations c ON cs.conv_id=c.id"
            " WHERE cs.device_id=? ORDER BY c.updated_at DESC LIMIT ?",
            (device_id, limit)).fetchall()
    return [_row_to_dict(r) for r in rows]


def save_memory_item(device_id: str, category: str, content: str,
                     source_conv_id: str = "") -> dict:
    rec = {"id": new_id("mem_"), "device_id": device_id, "category": category,
           "content": content, "source_conv_id": source_conv_id,
           "created_at": now(), "updated_at": now()}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO memory_items (id,device_id,category,content,source_conv_id,created_at,updated_at)"
            " VALUES (:id,:device_id,:category,:content,:source_conv_id,:created_at,:updated_at)", rec)
    return rec


def get_memory_items(device_id: str, limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM memory_items WHERE device_id=? ORDER BY updated_at DESC LIMIT ?",
            (device_id, limit)).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_old_memory(device_id: str, keep: int = 50) -> None:
    """保留最近 N 条记忆，删除更早的。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id FROM memory_items WHERE device_id=? ORDER BY updated_at DESC LIMIT 1 OFFSET ?",
            (device_id, keep)).fetchall()
        if rows:
            oldest_keep = rows[-1]["id"]
            conn.execute("DELETE FROM memory_items WHERE device_id=? AND updated_at < "
                         "(SELECT updated_at FROM memory_items WHERE id=?)",
                         (device_id, oldest_keep))


# ---------------- personal knowledge base （LLM WIKI 词条 + 反思报告） ----------------

def _tag_key(tag: str) -> str:
    """标签分组键：忽略大小写、空格与常见分隔符。"""
    return re.sub(r"[\s\-_/··]+", "", str(tag).strip().lower())


def _latin_stems(tag_key: str) -> set[str]:
    """提取键中的拉丁词干（如 api安全 → {api}），用于识别共享核心词的同义标签。"""
    return set(re.findall(r"[a-z]{2,}", tag_key))


def _stems_overlap(a: set[str], b: set[str]) -> bool:
    """词干子串级重叠：api ⊂ webapi 视为同族（处理 WEB API 这类无分隔的复合词）。"""
    return any(x in y or y in x for x in a for y in b)


def _normalize_tags(tags) -> list[str]:
    """保存时标签归一化：去品牌前缀（Sangfor/深信服）、合并同义写法（组内去重）、限数量。"""
    out, seen = [], set()
    for t in tags or []:
        t = re.sub(r"^(?:sangfor|深信服)[\s\-]*", "", str(t).strip(), flags=re.I)
        t = re.sub(r"\s+", " ", t).strip()
        k = _tag_key(t)
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(t[:20])
    return out[:6]


def _group_tags(entries: list[dict]) -> list[dict]:
    """标签分组：三种同义情形合并为一组，组内以频次最高的写法作展示名——
    ① 仅大小写/空格不同（af / AF）；② 归一化后互为包含（深信服AF / AF、API / WEB API）；
    ③ 共享同一拉丁核心词（API安全 / API开放接口 / API接入 / WEB API）。"""
    counts: dict[str, int] = {}
    for e in entries:
        for t in e.get("tags") or []:
            t = str(t).strip()
            if t:
                counts[t] = counts.get(t, 0) + 1
    groups: list[dict] = []
    for tag, cnt in sorted(counts.items(), key=lambda x: -x[1]):
        k = _tag_key(tag)
        stems = _latin_stems(k)
        for g in groups:
            gk = g["key"]
            if not k or not gk:
                continue
            if k in gk or gk in k or (stems and _stems_overlap(stems, g["stems"])):
                g["value"] += cnt
                g["members"].add(tag)
                g["stems"] |= stems
                break
        else:
            groups.append({"key": k, "rep": tag, "value": cnt,
                           "members": {tag}, "stems": set(stems)})
    return groups


def group_tags(entries: list[dict], top_n: int = 30) -> list[dict]:
    """标签分组计数（去重复口径）：[{name: 展示名, value: 组内总频次}]，按频次降序。"""
    groups = _group_tags(entries)
    return [{"name": g["rep"], "value": g["value"]}
            for g in sorted(groups, key=lambda x: -x["value"])[:top_n]]


def tag_display_map(entries: list[dict]) -> dict[str, str]:
    """raw 标签 → 其分组展示名（知识图谱连边用，使同义标签能互相连线）。"""
    d: dict[str, str] = {}
    for g in _group_tags(entries):
        for m in g["members"]:
            d[m] = g["rep"]
    return d


def _kb_entry_from_row(row) -> dict:
    d = _row_to_dict(row)
    for field, col in (("key_points", "key_points_json"), ("references", "references_json"),
                       ("tags", "tags_json"), ("aliases", "aliases_json")):
        try:
            d[field] = json.loads(d.get(col) or "[]")
        except (ValueError, TypeError):
            d[field] = []
    return d


def save_kb_entry(rec: dict) -> dict:
    data = {"id": rec.get("id") or new_id("kb_"), "conv_id": rec.get("conv_id", ""),
            "topic": rec["topic"], "category": rec.get("category") or "其他",
            "summary": rec.get("summary", ""), "content_md": rec.get("content_md", ""),
            "key_points_json": json.dumps(rec.get("key_points") or [], ensure_ascii=False),
            "references_json": json.dumps(rec.get("references") or [], ensure_ascii=False),
            "tags_json": json.dumps(_normalize_tags(rec.get("tags") or []), ensure_ascii=False),
            "aliases_json": json.dumps(_normalize_tags(rec.get("aliases") or []), ensure_ascii=False),
            "product": rec.get("product", ""), "created_at": now(), "updated_at": now()}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kb_entries (id,conv_id,topic,category,summary,content_md,"
            "key_points_json,references_json,tags_json,aliases_json,product,created_at,updated_at)"
            " VALUES (:id,:conv_id,:topic,:category,:summary,:content_md,"
            ":key_points_json,:references_json,:tags_json,:aliases_json,:product,:created_at,:updated_at)", data)
    return get_kb_entry(data["id"])


def get_kb_entry(entry_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM kb_entries WHERE id=?", (entry_id,)).fetchone()
    return _kb_entry_from_row(row) if row else None


def list_kb_entries(category: str = "", keyword: str = "", limit: int = 200) -> list[dict]:
    sql, args = "SELECT * FROM kb_entries WHERE 1=1", []
    if category:
        sql += " AND category=?"
        args.append(category)
    if keyword:
        sql += " AND (topic LIKE ? OR summary LIKE ? OR content_md LIKE ? OR tags_json LIKE ?)"
        args.extend([f"%{keyword}%"] * 4)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [_kb_entry_from_row(r) for r in rows]


def delete_kb_entry(entry_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM kb_entries WHERE id=?", (entry_id,))


def kb_entry_topic_exists(topic: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM kb_entries WHERE topic=? LIMIT 1", (topic,)).fetchone()
    return row is not None


def get_kb_entry_by_topic(topic: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM kb_entries WHERE topic=? ORDER BY updated_at DESC LIMIT 1",
                           (topic,)).fetchone()
    return _kb_entry_from_row(row) if row else None


def update_kb_entry_content(entry_id: str, conv_id: str, summary: str, content_md: str,
                            key_points: list, references: list, tags: list,
                            aliases: list | None = None) -> None:
    """同主题词条合并更新（Karpathy-Wiki 式记忆）：summary/正文以最新沉淀为准，
    要点/标签/引用/别名做并集去重——增量信息保留，不覆盖旧知识。保留原 id 与首次创建时间。"""
    row = None
    with _connect() as conn:
        row = conn.execute("SELECT * FROM kb_entries WHERE id=?", (entry_id,)).fetchone()
    if not row:
        return
    old = _kb_entry_from_row(row)

    def _union(old_list, new_list):
        out, seen = [], set()
        for item in (old_list or []) + (new_list or []):
            k = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
            if k not in seen:
                seen.add(k)
                out.append(item)
        return out

    merged_points = _union(old.get("key_points"), key_points)[:8]
    merged_refs = _union(old.get("references"), references)[:8]
    merged_tags = _normalize_tags(_union(old.get("tags"), tags))[:6]
    merged_aliases = _normalize_tags(_union(old.get("aliases"), aliases))[:8]

    with _connect() as conn:
        conn.execute(
            "UPDATE kb_entries SET conv_id=?, summary=?, content_md=?, key_points_json=?,"
            " references_json=?, tags_json=?, aliases_json=?, updated_at=? WHERE id=?",
            (conv_id or old.get("conv_id", ""), summary[:200], content_md,
             json.dumps(merged_points, ensure_ascii=False),
             json.dumps(merged_refs, ensure_ascii=False),
             json.dumps(merged_tags, ensure_ascii=False),
             json.dumps(merged_aliases, ensure_ascii=False),
             now(), entry_id))


def conv_kb_sedimented(conv_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM kb_entries WHERE conv_id=? LIMIT 1", (conv_id,)).fetchone()
    return row is not None


def list_pending_kb_convs() -> list[str]:
    """待沉淀对话：勾选过知识库或明确要求沉淀（KB 检索/沉淀登记审计）且尚未沉淀、未被忽略。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT conv_id FROM audit_logs"
            " WHERE action IN ('agent.tool.search_official_knowledge', 'agent.tool.record_to_kb')"
            " AND conv_id != ''"
            " AND conv_id NOT IN (SELECT DISTINCT conv_id FROM kb_entries)"
            " AND conv_id NOT IN (SELECT conv_id FROM kb_dismissed)"
            " ORDER BY ts DESC").fetchall()
    return [r["conv_id"] for r in rows]


def dismiss_kb_conv(conv_id: str) -> None:
    """把对话移出待沉淀队列（自动/手动沉淀均跳过）。"""
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO kb_dismissed (conv_id,created_at) VALUES (?,?)",
            (conv_id, now()))


def kb_conv_dismissed(conv_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM kb_dismissed WHERE conv_id=? LIMIT 1",
                           (conv_id,)).fetchone()
    return row is not None


def save_kb_reflection(content_md: str, stats: dict, period: str = "") -> dict:
    rec = {"id": new_id("kbr_"), "content_md": content_md,
           "stats_json": json.dumps(stats, ensure_ascii=False),
           "period": period, "created_at": now()}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kb_reflections (id,content_md,stats_json,period,created_at)"
            " VALUES (:id,:content_md,:stats_json,:period,:created_at)", rec)
    return rec


def list_kb_reflections(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM kb_reflections ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        try:
            d["stats"] = json.loads(d.get("stats_json") or "{}")
        except (ValueError, TypeError):
            d["stats"] = {}
        out.append(d)
    return out


def delete_kb_reflection(reflection_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM kb_reflections WHERE id=?", (reflection_id,))


def _tokenize_keyword(keyword: str) -> list[str]:
    """查询词分词：拉丁/数字串整体为一个词元，中文段做 2-gram（无分词依赖的中文检索）。"""
    tokens: set[str] = set()
    for seg in re.split(r"[\s,，。;；/、|？?！!]+", keyword or ""):
        if not seg:
            continue
        for m in re.findall(r"[A-Za-z0-9.\-]{2,}", seg):
            tokens.add(m.lower())
        han = re.sub(r"[A-Za-z0-9.\-]+", " ", seg)
        for chunk in han.split():
            if len(chunk) >= 2:
                for i in range(len(chunk) - 1):
                    tokens.add(chunk[i:i + 2])
            elif chunk:
                tokens.add(chunk)
    return [t for t in tokens if len(t) >= 2]


def search_kb_entries(keyword: str, limit: int = 5) -> list[dict]:
    """本地知识库相关度检索：词元多字段加权评分（topic×5 / tags×3 / summary×2 / 正文×1）。

    返回 [{词条字段..., "score": 分, "matched": [命中词元]}]，按分数降序。
    """
    tokens = _tokenize_keyword(keyword)
    if not tokens:
        return []
    scored = []
    for e in list_kb_entries(limit=1000):
        fields = [
            ((e.get("topic") or "").lower(), 5),
            (" ".join((e.get("aliases") or []) + (e.get("tags") or [])).lower(), 4),
            ((e.get("summary") or "").lower(), 2),
            ((e.get("content_md") or "").lower(), 1),
        ]
        score, hits = 0, []
        for tok in tokens:
            best = 0
            for text, w in fields:
                if tok in text and w > best:
                    best = w
            if best:
                score += best
                hits.append(tok)
        if score > 0:
            d = dict(e)
            d["score"] = score
            d["matched"] = hits
            scored.append((score, d))
    scored.sort(key=lambda x: -x[0])
    return [d for _score, d in scored[:limit]]


def kb_stats() -> dict:
    """个人知识库统计：总数/分类分布/标签分组计数/沉淀时间线（供可视化）。"""
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM kb_entries").fetchone()["c"]
        categories = [{"name": r["category"], "value": r["c"]} for r in conn.execute(
            "SELECT category, COUNT(*) AS c FROM kb_entries GROUP BY category ORDER BY c DESC")]
        timeline = [{"day": r["day"], "value": r["c"]} for r in conn.execute(
            "SELECT substr(created_at,1,10) AS day, COUNT(*) AS c FROM kb_entries"
            " GROUP BY day ORDER BY day")]
    tags = group_tags(list_kb_entries(limit=1000))
    return {"total": total, "categories": categories, "tags": tags,
            "timeline": timeline, "reflections": len(list_kb_reflections(limit=1000))}
