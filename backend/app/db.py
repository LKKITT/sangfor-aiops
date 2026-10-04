"""SQLite 数据访问层：设备、备份、待确认动作、审计日志、会话消息、更新缓存。

线程安全：thread-local 连接，WAL 模式；所有写入为轻量短事务。
"""
import json
import re
from datetime import datetime, timedelta
from typing import Any, Optional

# 共享基础设施 re-export：保持 db.* 调用方（services/api/tests）零改动
from app.dbcore import (SCHEMA, _connect, _local, _row_to_dict, audit, backup_file_path,  # noqa: F401
                        get_setting, get_settings, init_db, new_id, now, set_setting)  # noqa: F401








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



def cleanup_expired(retention_days: int = 180, audit_days: int = 365) -> dict:
    """数据保留清理：过期会话（含消息/摘要/沉淀状态/已处理待确认）与审计日志。

    阈值 0 表示对应项不清理；pending 状态的待确认动作永不自动删除；
    指向过期会话的渠道绑定仅解绑不删除。返回各表实际删除行数。
    """
    removed: dict[str, int] = {}
    conv_cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat(timespec="seconds")
    audit_cutoff = (datetime.now() - timedelta(days=audit_days)).isoformat(timespec="seconds")
    with _connect() as conn:
        old_convs = [r["id"] for r in conn.execute(
            "SELECT id FROM conversations WHERE updated_at < ?", (conv_cutoff,))]
        if old_convs and retention_days > 0:
            q = ",".join("?" * len(old_convs))
            args = (*old_convs,)
            removed["messages"] = conn.execute(
                f"DELETE FROM messages WHERE conv_id IN ({q})", args).rowcount
            conn.execute(f"DELETE FROM conv_summaries WHERE conv_id IN ({q})", args)
            conn.execute(f"DELETE FROM kb_sediment_status WHERE conv_id IN ({q})", args)
            conn.execute(f"DELETE FROM pending_actions WHERE conv_id IN ({q}) AND status != 'pending'", args)
            conn.execute(f"UPDATE channel_bindings SET conv_id = '' WHERE conv_id IN ({q})", args)
            removed["conversations"] = conn.execute(
                f"DELETE FROM conversations WHERE id IN ({q})", args).rowcount
        if audit_days > 0:
            removed["audit_logs"] = conn.execute(
                "DELETE FROM audit_logs WHERE ts < ?", (audit_cutoff,)).rowcount
    return removed


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
                  COALESCE(d.name, nd.name,
                          CASE WHEN c.device_id = 'global' THEN '全局' END) AS device_name
                FROM conversations c
                LEFT JOIN devices d ON d.id = c.device_id
                LEFT JOIN netdev_devices nd ON nd.id = c.device_id
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
                 since_id: Optional[int] = None,
                 before_id: Optional[int] = None) -> list[dict]:
    """会话消息（按 id 升序）。limit=只取最近 N 条；since_id=只取该 id 之后的新消息；
    before_id=只取该 id 之前的消息（配合 limit 做向上翻页游标）。"""
    with _connect() as conn:
        if before_id is not None:
            if limit:   # 向上翻页：取 before_id 之前最近 limit 条，再按 id 升序返回
                rows = conn.execute(
                    "SELECT * FROM (SELECT * FROM messages WHERE conv_id=? AND id<?"
                    " ORDER BY id DESC LIMIT ?) ORDER BY id", (conv_id, before_id, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE conv_id=? AND id<? ORDER BY id",
                    (conv_id, before_id)).fetchall()
            out = []
            for r in rows:
                d = _row_to_dict(r)
                d["content"] = json.loads(d.pop("content_json"))
                out.append(d)
            return out
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


def list_kb_entries(category: str = "", keyword: str = "", limit: int = 200,
                    order: str = "created") -> list[dict]:
    """词条列表。order: created=按创建时间（默认）/ updated=按最近更新（合并更新可见）。"""
    sql, args = "SELECT * FROM kb_entries WHERE 1=1", []
    if category:
        sql += " AND category=?"
        args.append(category)
    if keyword:
        sql += " AND (topic LIKE ? OR summary LIKE ? OR content_md LIKE ? OR tags_json LIKE ?)"
        args.extend([f"%{keyword}%"] * 4)
    order_col = "updated_at" if order == "updated" else "created_at"
    sql += f" ORDER BY {order_col} DESC LIMIT ?"
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
    """待沉淀对话：官方知识库实际命中（agent.kb.hit）或用户明确要求沉淀（record_to_kb），
    且尚未沉淀、未被忽略、最近一次沉淀不是"已完成但零产出/无沉淀价值"。

    会话再次命中知识库时会重新进入沉淀流程（进度状态被重置），自动重新出现。
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT conv_id FROM audit_logs"
            " WHERE action IN ('agent.kb.hit', 'agent.tool.record_to_kb')"
            " AND conv_id != ''"
            " AND conv_id NOT IN (SELECT DISTINCT conv_id FROM kb_entries)"
            " AND conv_id NOT IN (SELECT conv_id FROM kb_dismissed)"
            " AND conv_id NOT IN (SELECT conv_id FROM kb_sediment_status"
            "                     WHERE status='skipped'"
            "                        OR (status='done' AND saved=0))"
            " ORDER BY ts DESC").fetchall()
    return [r["conv_id"] for r in rows]


def conv_has_audit(conv_id: str, action: str) -> bool:
    """会话是否存在某类审计记录（用于区分用户明确要求的沉淀与知识库自动命中）。"""
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM audit_logs WHERE conv_id=? AND action=? LIMIT 1",
                           (conv_id, action)).fetchone()
    return row is not None


def save_kb_sediment_status(conv_id: str, status: str, note: str = "", saved: int = 0) -> None:
    """记录会话知识沉淀进度（待沉淀列表展示用）。"""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kb_sediment_status (conv_id,status,note,saved,updated_at)"
            " VALUES (:conv_id,:status,:note,:saved,:updated_at)"
            " ON CONFLICT(conv_id) DO UPDATE SET status=:status, note=:note, saved=:saved,"
            " updated_at=:updated_at",
            {"conv_id": conv_id, "status": status, "note": note, "saved": saved,
             "updated_at": now()})


def get_kb_sediment_status(conv_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM kb_sediment_status WHERE conv_id=?",
                           (conv_id,)).fetchone()
    return _row_to_dict(row) if row else None


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
    with _connect() as conn:
        reflections = conn.execute("SELECT COUNT(*) AS c FROM kb_reflections").fetchone()["c"]
    return {"total": total, "categories": categories, "tags": tags,
            "timeline": timeline, "reflections": reflections}


# ---------------- 网络设备管理（SSH 交换机/路由器） ----------------

def list_netdev_devices(group: str = "") -> list[dict]:
    with _connect() as conn:
        if group:
            rows = conn.execute("SELECT * FROM netdev_devices WHERE group_name=? ORDER BY created_at",
                                (group,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM netdev_devices ORDER BY created_at").fetchall()
    return [_row_to_dict(r) for r in rows]


def get_netdev_device(device_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM netdev_devices WHERE id=?", (device_id,)).fetchone()
    return _row_to_dict(row) if row else None


def save_netdev_device(data: dict) -> dict:
    data = {"name": "", "vendor": "huawei", "model": "", "port": 22, "username": "",
            "password": "", "enable_password": "", "group_name": "", "last_ok_at": "",
            **data, "id": data.get("id") or new_id("nd_"),
            "created_at": data.get("created_at") or now()}
    with _connect() as conn:
        # 显式 id（编辑已有设备）：直接按 id 更新（允许修改 host/port）
        row = conn.execute("SELECT id FROM netdev_devices WHERE id=?",
                           (data["id"],)).fetchone() if data.get("id") else None
        if row:
            conn.execute(
                "UPDATE netdev_devices SET name=:name, vendor=:vendor, model=:model,"
                " host=:host, port=:port, username=:username, password=:password,"
                " enable_password=:enable_password, group_name=:group_name WHERE id=:id", data)
            return get_netdev_device(data["id"])
        # 同 host:port 幂等保存：已有记录沿用其 id（批量导入重复行=更新而非新建）
        row = conn.execute("SELECT id FROM netdev_devices WHERE host=? AND port=?",
                           (data["host"], data["port"])).fetchone()
        if row:
            data["id"] = row["id"]
        conn.execute(
            "INSERT INTO netdev_devices (id,name,vendor,model,host,port,username,password,"
            "enable_password,group_name,last_ok_at,created_at)"
            " VALUES (:id,:name,:vendor,:model,:host,:port,:username,:password,"
            ":enable_password,:group_name,:last_ok_at,:created_at)"
            " ON CONFLICT(host, port) DO UPDATE SET name=:name, vendor=:vendor, model=:model,"
            " username=:username, password=:password, enable_password=:enable_password,"
            " group_name=:group_name",
            data)
    return get_netdev_device(data["id"])


def delete_netdev_device(device_id: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM netdev_devices WHERE id=?", (device_id,))


def update_netdev_last_ok(device_id: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE netdev_devices SET last_ok_at=? WHERE id=?", (now(), device_id))


def create_netdev_task(task: dict) -> dict:
    rec = {"id": new_id("ndt_"), "name": task.get("name", ""), "status": "running",
           "timeout": float(task.get("timeout", 30)), "created_at": now(), "finished_at": "",
           "commands": json.dumps(task.get("commands") or [], ensure_ascii=False),
           "device_ids": json.dumps(task.get("device_ids") or [], ensure_ascii=False)}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO netdev_tasks (id,name,commands,device_ids,status,timeout,created_at,finished_at)"
            " VALUES (:id,:name,:commands,:device_ids,:status,:timeout,:created_at,:finished_at)", rec)
    return rec


def save_netdev_task_item(item: dict) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO netdev_task_items (id,task_id,device_id,device_name,status,output,error,duration,finished_at)"
            " VALUES (:id,:task_id,:device_id,:device_name,:status,:output,:error,:duration,:finished_at)",
            {"id": item.get("id") or new_id("ndi_"), "task_id": item["task_id"],
             "device_id": item["device_id"], "device_name": item.get("device_name", ""),
             "status": item.get("status", "pending"), "output": item.get("output", ""),
             "error": item.get("error", ""), "duration": float(item.get("duration", 0)),
             "finished_at": item.get("finished_at", "") or now()})


def finish_netdev_task(task_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE netdev_tasks SET status=?, finished_at=? WHERE id=?",
                     (status, now(), task_id))


def list_netdev_tasks(limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM netdev_tasks ORDER BY created_at DESC LIMIT ?",
                            (limit,)).fetchall()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["commands"] = json.loads(d.get("commands") or "[]")
        d["device_ids"] = json.loads(d.get("device_ids") or "[]")
        out.append(d)
    return out


def get_netdev_task(task_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM netdev_tasks WHERE id=?", (task_id,)).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    d["commands"] = json.loads(d.get("commands") or "[]")
    d["device_ids"] = json.loads(d.get("device_ids") or "[]")
    d["items"] = []
    for it in conn.execute("SELECT * FROM netdev_task_items WHERE task_id=? ORDER BY id",
                           (task_id,)).fetchall():
        d["items"].append(_row_to_dict(it))
    return d


# ---------------- 网络拓扑（LLDP/ARP 快照与布局位置） ----------------

def get_netdev_topology_cache(device_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM netdev_topology_cache WHERE device_id=?",
                           (device_id,)).fetchone()
    if not row:
        return None
    d = _row_to_dict(row)
    d["lldp"] = json.loads(d.get("lldp_json") or "[]")
    d["arp"] = json.loads(d.get("arp_json") or "[]")
    d["mac"] = json.loads(d.get("mac_json") or "[]")
    return d


def save_netdev_topology_cache(device_id: str, group_name: str,
                               lldp: list, arp: list, mac: list | None = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO netdev_topology_cache (device_id, group_name, lldp_json, arp_json, mac_json, fetched_at)"
            " VALUES (:device_id, :group_name, :lldp_json, :arp_json, :mac_json, :fetched_at)"
            " ON CONFLICT(device_id) DO UPDATE SET group_name=:group_name, lldp_json=:lldp_json,"
            " arp_json=:arp_json, mac_json=:mac_json, fetched_at=:fetched_at",
            {"device_id": device_id, "group_name": group_name,
             "lldp_json": json.dumps(lldp, ensure_ascii=False),
             "arp_json": json.dumps(arp, ensure_ascii=False),
             "mac_json": json.dumps(mac or [], ensure_ascii=False), "fetched_at": now()})


def list_netdev_topology_cache(group: str = "") -> list[dict]:
    with _connect() as conn:
        if group:
            rows = conn.execute("SELECT * FROM netdev_topology_cache WHERE group_name=?",
                                (group,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM netdev_topology_cache").fetchall()
    out = []
    for r in rows:
        d = _row_to_dict(r)
        d["lldp"] = json.loads(d.get("lldp_json") or "[]")
        d["arp"] = json.loads(d.get("arp_json") or "[]")
        d["mac"] = json.loads(d.get("mac_json") or "[]")
        out.append(d)
    return out


def get_netdev_topology_positions(group: str) -> dict:
    """返回 {device_id: [x, y]}（手动布局坐标）。"""
    with _connect() as conn:
        rows = conn.execute("SELECT device_id, x, y FROM netdev_topology_pos WHERE group_name=?",
                            (group,)).fetchall()
    return {r["device_id"]: [r["x"], r["y"]] for r in rows}


def save_netdev_topology_positions(group: str, positions: dict) -> None:
    """positions: {device_id: [x, y]}，整组覆盖保存。"""
    with _connect() as conn:
        conn.execute("DELETE FROM netdev_topology_pos WHERE group_name=?", (group,))
        conn.executemany(
            "INSERT INTO netdev_topology_pos (group_name, device_id, x, y, updated_at)"
            " VALUES (?,?,?,?,?)",
            [(group, str(dev_id), float(pos[0]), float(pos[1]), now())
             for dev_id, pos in (positions or {}).items()])
