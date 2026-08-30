"""SQLite 数据访问层：设备、备份、待确认动作、审计日志、会话消息、更新缓存。

线程安全：thread-local 连接，WAL 模式；所有写入为轻量短事务。
"""
import json
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

def create_conversation(title: str = "新对话") -> dict:
    conv = {"id": new_id("conv_"), "title": title[:40], "created_at": now(), "updated_at": now()}
    with _connect() as conn:
        conn.execute("INSERT INTO conversations (id,title,created_at,updated_at) VALUES (:id,:title,:created_at,:updated_at)", conv)
    return conv


def get_conversation(conv_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
    return _row_to_dict(row) if row else None


def touch_conversation(conv_id: str, title: Optional[str] = None) -> None:
    with _connect() as conn:
        if title:
            conn.execute("UPDATE conversations SET updated_at=?, title=? WHERE id=? AND title='新对话'",
                         (now(), title[:40], conv_id))
        else:
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conv_id))


def list_conversations(limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_dict(r) for r in rows]


def add_message(conv_id: str, role: str, content: dict) -> None:
    with _connect() as conn:
        conn.execute("INSERT INTO messages (conv_id,role,content_json,created_at) VALUES (?,?,?,?)",
                     (conv_id, role, json.dumps(content, ensure_ascii=False), now()))
    touch_conversation(conv_id)


def get_messages(conv_id: str) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM messages WHERE conv_id=? ORDER BY id", (conv_id,)).fetchall()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["content"] = json.loads(d.pop("content_json"))
        out.append(d)
    return out


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


# ---------------- backup file helpers ----------------

def backup_file_path(backup_id: str, suffix: str = ".conf") -> Path:
    p = settings.backup_dir / f"{backup_id}{suffix}"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p
