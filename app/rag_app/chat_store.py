"""
AI知识库 - 聊天记录持久化存储（SQLite）
替代 localStorage，支持多会话、CRUD、自动迁移
"""
import sqlite3
import json
import os
import threading
import time
from typing import List, Dict, Optional, Any
from app.rag_app.config import Config

DB_PATH = os.path.join(Config.ROUTES_DATA_DIR, "chat_history.db")
_lock = threading.Lock()

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '新对话',
    library_id  TEXT NOT NULL DEFAULT 'all',
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content     TEXT NOT NULL,
    citations   TEXT DEFAULT '[]',
    grounding_sources TEXT DEFAULT '[]',
    evidence    TEXT DEFAULT '[]',
    follow_ups  TEXT DEFAULT '[]',
    web_supplemented INTEGER DEFAULT 0,
    scope       TEXT DEFAULT '',
    custom_doc_ids TEXT DEFAULT '[]',
    timestamp   TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
"""


def _get_conn() -> sqlite3.Connection:
    os.makedirs(Config.ROUTES_DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock:
        conn = _get_conn()
        conn.executescript(SCHEMA_SQL)
        # ⛔ 2026-08-13：存量库迁移 —— 补 evidence/followUps/webSupplemented/scope/customDocIds 列
        cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
        for col, ddl in {
            "evidence": "ALTER TABLE messages ADD COLUMN evidence TEXT DEFAULT '[]'",
            "follow_ups": "ALTER TABLE messages ADD COLUMN follow_ups TEXT DEFAULT '[]'",
            "web_supplemented": "ALTER TABLE messages ADD COLUMN web_supplemented INTEGER DEFAULT 0",
            "scope": "ALTER TABLE messages ADD COLUMN scope TEXT DEFAULT ''",
            "custom_doc_ids": "ALTER TABLE messages ADD COLUMN custom_doc_ids TEXT DEFAULT '[]'",
        }.items():
            if col not in cols:
                conn.execute(ddl)
        conn.commit()
        conn.close()


def save_session(session_id: str, title: str, messages: List[dict], library_id: str = "all") -> dict:
    """保存或更新一个完整会话（含消息）"""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        conn = _get_conn()
        try:
            # upsert session
            conn.execute("""
                INSERT INTO sessions (id, title, library_id, message_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    library_id=excluded.library_id,
                    message_count=excluded.message_count,
                    updated_at=excluded.updated_at
            """, (session_id, title, library_id, len(messages), now, now))

            # delete old messages and re-insert
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            for msg in messages:
                conn.execute("""
                INSERT INTO messages (id, session_id, role, content, citations, grounding_sources, timestamp,
                                      evidence, follow_ups, web_supplemented, scope, custom_doc_ids)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    msg.get("id", ""),
                    session_id,
                    msg.get("role", "user"),
                    msg.get("content", ""),
                    json.dumps(msg.get("citations", []), ensure_ascii=False),
                    json.dumps(msg.get("groundingSources", []), ensure_ascii=False),
                    msg.get("timestamp", now),
                    json.dumps(msg.get("evidence", []), ensure_ascii=False),
                    json.dumps(msg.get("followUps", []), ensure_ascii=False),
                    1 if msg.get("webSupplemented") else 0,
                    msg.get("scope", "") or "",
                    json.dumps(msg.get("customDocIds", []), ensure_ascii=False),
                ))
            conn.commit()
            return {"status": "ok", "session_id": session_id, "message_count": len(messages)}
        finally:
            conn.close()


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """获取单个会话及其消息"""
    conn = _get_conn()
    try:
        s = conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not s:
            return None
        msgs = conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY timestamp ASC",
            (session_id,)
        ).fetchall()
        return {
            "id": s["id"],
            "title": s["title"],
            "libraryId": s["library_id"],
            "messageCount": s["message_count"],
            "createdAt": s["created_at"],
            "updatedAt": s["updated_at"],
            "messages": [_row_to_message(m) for m in msgs],
        }
    finally:
        conn.close()


def list_sessions(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """列出所有会话（不含消息体，仅元数据）"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [{
            "id": r["id"],
            "title": r["title"],
            "libraryId": r["library_id"],
            "messageCount": r["message_count"],
            "createdAt": r["created_at"],
            "updatedAt": r["updated_at"],
            "preview": _get_preview(r["id"], conn),
        } for r in rows]
    finally:
        conn.close()


def delete_session(session_id: str) -> dict:
    """删除单个会话"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            conn.commit()
            return {"status": "ok", "deleted": session_id}
        finally:
            conn.close()


def clear_all_sessions() -> dict:
    """清空全部会话"""
    with _lock:
        conn = _get_conn()
        try:
            conn.execute("DELETE FROM messages")
            conn.execute("DELETE FROM sessions")
            conn.commit()
            return {"status": "ok", "cleared": True}
        finally:
            conn.close()


def _get_preview(session_id: str, conn: sqlite3.Connection) -> str:
    """取最近一条用户消息的前50字作为预览"""
    row = conn.execute(
        "SELECT content FROM messages WHERE session_id=? AND role='user' ORDER BY timestamp DESC LIMIT 1",
        (session_id,)
    ).fetchone()
    if row and row["content"]:
        text = row["content"].replace("\n", " ").strip()
        return text[:50] + ("..." if len(text) > 50 else "")
    return ""


def _row_to_message(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "citations": json.loads(row["citations"] or "[]"),
        "groundingSources": json.loads(row["grounding_sources"] or "[]"),
        "evidence": json.loads(row["evidence"] or "[]"),
        "followUps": json.loads(row["follow_ups"] or "[]"),
        "webSupplemented": bool(row["web_supplemented"]),
        "scope": row["scope"] or None,
        "customDocIds": json.loads(row["custom_doc_ids"] or "[]"),
        "timestamp": row["timestamp"],
    }


def total_sessions() -> int:
    conn = _get_conn()
    try:
        return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    finally:
        conn.close()
