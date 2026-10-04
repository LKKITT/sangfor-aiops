"""数据库共享基础设施：线程本地连接、SCHEMA、初始化与通用工具。

领域 repo（当前集中于 db.py，后续可按域渐进拆分至 app/repos/）一律从本模块
取基础设施，禁止 repo 之间互相 import。db.py 顶部 re-export 保持调用方零改动。
"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path

from app.config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'af',            -- af / ac（可扩展）
    mode TEXT NOT NULL DEFAULT 'real',          -- real（模拟器演示模式已下线）
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
CREATE TABLE IF NOT EXISTS kb_sediment_status (
    conv_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,                        -- pending / running / done / skipped / failed
    note TEXT NOT NULL DEFAULT '',               -- 进度说明（完成条数/跳过或失败原因）
    saved INTEGER NOT NULL DEFAULT 0,            -- 本次沉淀新增词条数
    updated_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS netdev_devices (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vendor TEXT NOT NULL DEFAULT 'huawei',       -- huawei / h3c / cisco / ruijie / zte / other
    model TEXT NOT NULL DEFAULT '',
    host TEXT NOT NULL,                          -- 管理地址（IP/域名）
    port INTEGER NOT NULL DEFAULT 22,
    username TEXT NOT NULL DEFAULT '',
    password TEXT NOT NULL DEFAULT '',
    enable_password TEXT NOT NULL DEFAULT '',    -- enable/super 口令（可选）
    group_name TEXT NOT NULL DEFAULT '',         -- 分组（如"总部核心"）
    last_ok_at TEXT NOT NULL DEFAULT '',         -- 最近一次连接测试成功时间
    created_at TEXT NOT NULL,
    UNIQUE(host, port)
);
CREATE TABLE IF NOT EXISTS netdev_tasks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    commands TEXT NOT NULL,                      -- JSON 数组：待执行命令列表
    device_ids TEXT NOT NULL,                    -- JSON 数组：目标设备 id
    status TEXT NOT NULL DEFAULT 'running',      -- running / done / failed
    timeout REAL NOT NULL DEFAULT 30,
    created_at TEXT NOT NULL,
    finished_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS netdev_task_items (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    device_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',      -- pending / running / ok / failed
    output TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0,
    finished_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS netdev_topology_cache (
    device_id TEXT PRIMARY KEY,                  -- 每设备一份 LLDP/ARP 采集快照
    group_name TEXT NOT NULL DEFAULT '',
    lldp_json TEXT NOT NULL DEFAULT '[]',        -- [{local_port, neighbor, neighbor_port}]
    arp_json TEXT NOT NULL DEFAULT '[]',         -- [{ip, mac, port, vlan}]
    mac_json TEXT NOT NULL DEFAULT '[]',         -- [{mac, port, vlan}] MAC 地址表（接入定位）
    fetched_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS netdev_topology_pos (
    group_name TEXT NOT NULL,
    device_id TEXT NOT NULL,
    x REAL NOT NULL DEFAULT 0,
    y REAL NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (group_name, device_id)
);
-- 高频非主键过滤/关联列索引（IF NOT EXISTS 对存量库幂等）
CREATE INDEX IF NOT EXISTS idx_messages_conv        ON messages(conv_id);
CREATE INDEX IF NOT EXISTS idx_backups_device       ON backups(device_id);
CREATE INDEX IF NOT EXISTS idx_audit_conv           ON audit_logs(conv_id);
CREATE INDEX IF NOT EXISTS idx_audit_ts             ON audit_logs(ts);
CREATE INDEX IF NOT EXISTS idx_pending_conv         ON pending_actions(conv_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_device        ON memory_items(device_id);
CREATE INDEX IF NOT EXISTS idx_kb_topic             ON kb_entries(topic);
CREATE INDEX IF NOT EXISTS idx_kb_updated           ON kb_entries(updated_at);
CREATE INDEX IF NOT EXISTS idx_netdev_task_items    ON netdev_task_items(task_id);
CREATE INDEX IF NOT EXISTS idx_conversations_device ON conversations(device_id);
"""


def _connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(str(settings.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        # 不开启 foreign_keys：SCHEMA 未声明任何外键，级联删除由业务层手工完成，开了也是空转
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
        topo_cols = [r["name"] for r in conn.execute("PRAGMA table_info(netdev_topology_cache)")]
        if topo_cols and "mac_json" not in topo_cols:
            conn.execute("ALTER TABLE netdev_topology_cache ADD COLUMN mac_json TEXT NOT NULL DEFAULT '[]'")


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def audit(action: str, detail: dict | None = None, conv_id: str = "", device_id: str = "",
          actor: str = "agent", result: str = "ok") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO audit_logs (ts,conv_id,device_id,actor,action,detail_json,result) VALUES (?,?,?,?,?,?,?)",
            (now(), conv_id, device_id, actor, action, json.dumps(detail or {}, ensure_ascii=False), result),
        )


def get_setting(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def get_settings(keys: list[str]) -> dict[str, str]:
    """一次取多个设置项（合并为单条 IN 查询，避免逐 key 往返）。"""
    if not keys:
        return {}
    with _connect() as conn:
        q = ",".join("?" * len(keys))
        return {r["key"]: r["value"] for r in
                conn.execute(f"SELECT key, value FROM app_settings WHERE key IN ({q})", keys)}


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
