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
                       ("tags", "tags_json")):
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
            "product": rec.get("product", ""), "created_at": now(), "updated_at": now()}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kb_entries (id,conv_id,topic,category,summary,content_md,"
            "key_points_json,references_json,tags_json,product,created_at,updated_at)"
            " VALUES (:id,:conv_id,:topic,:category,:summary,:content_md,"
            ":key_points_json,:references_json,:tags_json,:product,:created_at,:updated_at)", data)
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


def conv_kb_sedimented(conv_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM kb_entries WHERE conv_id=? LIMIT 1", (conv_id,)).fetchone()
    return row is not None


def list_pending_kb_convs() -> list[str]:
    """勾选过知识库（审计中有 KB 工具调用）且尚未沉淀、未被忽略的对话 ID。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT conv_id FROM audit_logs"
            " WHERE action='agent.tool.search_official_knowledge' AND conv_id != ''"
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
