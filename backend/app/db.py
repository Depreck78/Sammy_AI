import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from .config import DB_PATH
from .security import decrypt_json, encrypt_json


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_message_search(conn: sqlite3.Connection) -> None:
    try:
        conn.executescript(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                message_id UNINDEXED,
                conversation_id UNINDEXED,
                role UNINDEXED,
                content,
                tokenize = 'unicode61'
            );
            CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(message_id, conversation_id, role, content)
                VALUES (new.id, new.conversation_id, new.role, new.content);
            END;
            CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
                DELETE FROM messages_fts WHERE message_id = old.id;
            END;
            CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE OF content ON messages BEGIN
                DELETE FROM messages_fts WHERE message_id = old.id;
                INSERT INTO messages_fts(message_id, conversation_id, role, content)
                VALUES (new.id, new.conversation_id, new.role, new.content);
            END;
            """
        )
        conn.execute("DELETE FROM messages_fts")
        conn.execute(
            """
            INSERT INTO messages_fts(message_id, conversation_id, role, content)
            SELECT id, conversation_id, role, content FROM messages
            WHERE role IN ('user', 'assistant')
            """
        )
    except sqlite3.OperationalError:
        # Some custom SQLite builds omit FTS5. Search falls back to LIKE.
        return


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                model TEXT,
                icon TEXT NOT NULL DEFAULT '',
                enabled_tools TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                model TEXT,
                agent_id TEXT,
                mode TEXT NOT NULL DEFAULT 'chat',
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT 'fact',
                content TEXT NOT NULL,
                normalized_content TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                confidence REAL NOT NULL DEFAULT 1.0,
                sensitive INTEGER NOT NULL DEFAULT 0,
                source_conversation_id TEXT,
                source_message_id TEXT,
                source_label TEXT NOT NULL DEFAULT '',
                expires_at TEXT,
                last_used_at TEXT,
                use_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_memories_lookup
            ON memories(scope, agent_id, status, updated_at);

            CREATE TABLE IF NOT EXISTS plugin_proposals (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                source_user_message_id TEXT NOT NULL DEFAULT '',
                service_name TEXT NOT NULL,
                goal TEXT NOT NULL,
                capabilities TEXT NOT NULL DEFAULT '[]',
                base_url TEXT NOT NULL,
                documentation_url TEXT NOT NULL DEFAULT '',
                auth_type TEXT NOT NULL DEFAULT 'none',
                write_access INTEGER NOT NULL DEFAULT 0,
                allow_private_network INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                plugin_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_plugin_proposals_conversation
            ON plugin_proposals(conversation_id, status, updated_at);

            CREATE TABLE IF NOT EXISTS tool_credentials (
                tool_name TEXT PRIMARY KEY,
                encrypted_credentials TEXT NOT NULL,
                connected INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                path TEXT NOT NULL,
                content_type TEXT,
                size INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_states (
                state TEXT PRIMARY KEY,
                tool_name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS besties (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                personality TEXT NOT NULL DEFAULT '',
                avatar TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _ensure_message_search(conn)
    _ensure_agent_icon_column()
    _ensure_conversation_mode_column()
    _ensure_plugin_proposal_columns()
    seed_defaults()


def _ensure_agent_icon_column() -> None:
    with connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
        if "icon" not in columns:
            conn.execute("ALTER TABLE agents ADD COLUMN icon TEXT NOT NULL DEFAULT ''")


def _ensure_conversation_mode_column() -> None:
    with connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(conversations)").fetchall()}
        if "mode" not in columns:
            conn.execute("ALTER TABLE conversations ADD COLUMN mode TEXT NOT NULL DEFAULT 'chat'")


def _ensure_plugin_proposal_columns() -> None:
    with connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(plugin_proposals)").fetchall()}
        if "source_user_message_id" not in columns:
            conn.execute(
                "ALTER TABLE plugin_proposals ADD COLUMN source_user_message_id TEXT NOT NULL DEFAULT ''"
            )
        if "write_access" not in columns:
            conn.execute("ALTER TABLE plugin_proposals ADD COLUMN write_access INTEGER NOT NULL DEFAULT 0")


DEFAULT_SETTINGS: Dict[str, Any] = {
    "default_model": "",
    "system_prompt": (
        "You are Sammy, a local AI agent running on this Mac. Be helpful, concise, "
        "and explicit when you use tools."
    ),
    "num_ctx": 8192,
    "num_predict": 2048,
    "temperature": 0.2,
    "think": False,
    "theme": "light",
    "access_password_hash": "",
    "memory_mode": "auto",
    "memory_recall_enabled": True,
    "memory_recall_limit": 5,
    "elevenlabs_enabled": False,
    "elevenlabs_voice_id": "",
    "voice_auth_enabled": False,
    "picovoice_access_key": "",
    "picovoice_speaker_profile": "",
    # Active "bestie" identity (empty = built-in Sammy). Gemini key is stored encrypted.
    "active_bestie_id": "",
    "gemini_api_key_enc": "",
    # Custom mDNS alias so phones can use http://<alias>.local without renaming the Mac.
    "network_alias": "sammy",
}


DEFAULT_AGENTS = [
    {
        "id": "default",
        "name": "Sammy",
        "system_prompt": DEFAULT_SETTINGS["system_prompt"],
        "model": "",
        "icon": "brain",
        "enabled_tools": ["web_search", "filesystem", "excel", "numbers"],
    },
    {
        "id": "email-manager",
        "name": "Email Manager",
        "system_prompt": (
            "You are Sammy's email manager. Help triage, search, draft, send, and reply to "
            "email. When replying to an existing email, preserve the thread. Only send after "
            "confirming recipient and intent."
        ),
        "model": "",
        "icon": "mail",
        "enabled_tools": ["gmail", "google_contacts", "web_search"],
    },
]


def seed_defaults() -> None:
    with connect() as conn:
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, json.dumps(value)),
            )
        for agent in DEFAULT_AGENTS:
            stamp = now_iso()
            conn.execute(
                """
                INSERT OR IGNORE INTO agents(
                    id, name, system_prompt, model, icon, enabled_tools, created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent["id"],
                    agent["name"],
                    agent["system_prompt"],
                    agent["model"],
                    agent.get("icon") or "",
                    json.dumps(agent["enabled_tools"]),
                    stamp,
                    stamp,
                ),
            )


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def get_settings() -> Dict[str, Any]:
    with connect() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    for row in rows:
        try:
            settings[row["key"]] = json.loads(row["value"])
        except json.JSONDecodeError:
            settings[row["key"]] = row["value"]
    return settings


def update_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    with connect() as conn:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO settings(key, value) VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, json.dumps(value)),
            )
    return get_settings()


def _agent_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    agent = dict(row)
    agent["icon"] = agent.get("icon") or ""
    try:
        agent["enabled_tools"] = json.loads(agent.get("enabled_tools") or "[]")
    except json.JSONDecodeError:
        agent["enabled_tools"] = []
    return agent


def list_agents() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM agents ORDER BY created_at ASC").fetchall()
    return [_agent_from_row(row) for row in rows]


def get_agent(agent_id: Optional[str]) -> Dict[str, Any]:
    if not agent_id:
        agent_id = "default"
    with connect() as conn:
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM agents WHERE id = 'default'").fetchone()
    return _agent_from_row(row)


def save_agent(data: Dict[str, Any], agent_id: Optional[str] = None) -> Dict[str, Any]:
    stamp = now_iso()
    enabled_tools = json.dumps(data.get("enabled_tools") or [])
    if agent_id:
        with connect() as conn:
            conn.execute(
                """
                UPDATE agents
                SET name = ?, system_prompt = ?, model = ?, icon = ?, enabled_tools = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    data["name"],
                    data.get("system_prompt") or "",
                    data.get("model") or "",
                    data.get("icon") or "",
                    enabled_tools,
                    stamp,
                    agent_id,
                ),
            )
        return get_agent(agent_id)
    agent_id = new_id()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO agents(id, name, system_prompt, model, icon, enabled_tools, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                agent_id,
                data["name"],
                data.get("system_prompt") or "",
                data.get("model") or "",
                data.get("icon") or "",
                enabled_tools,
                stamp,
                stamp,
            ),
        )
    return get_agent(agent_id)


def add_enabled_tools(agent_ids: List[str], tool_names: List[str]) -> None:
    if not agent_ids or not tool_names:
        return
    stamp = now_iso()
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, enabled_tools FROM agents WHERE id IN ({','.join('?' for _ in agent_ids)})",
            agent_ids,
        ).fetchall()
        for row in rows:
            try:
                enabled = json.loads(row["enabled_tools"] or "[]")
            except json.JSONDecodeError:
                enabled = []
            changed = False
            for tool_name in tool_names:
                if tool_name not in enabled:
                    enabled.append(tool_name)
                    changed = True
            if changed:
                conn.execute(
                    "UPDATE agents SET enabled_tools = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(enabled), stamp, row["id"]),
                )


def delete_agent(agent_id: str) -> None:
    if agent_id == "default":
        return
    with connect() as conn:
        conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))


def _bestie_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    bestie = dict(row)
    bestie["personality"] = bestie.get("personality") or ""
    bestie["avatar"] = bestie.get("avatar") or ""
    return bestie


def list_besties() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM besties ORDER BY created_at ASC").fetchall()
    return [_bestie_from_row(row) for row in rows]


def get_bestie(bestie_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not bestie_id:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM besties WHERE id = ?", (bestie_id,)).fetchone()
    return _bestie_from_row(row) if row is not None else None


def save_bestie(data: Dict[str, Any], bestie_id: Optional[str] = None) -> Dict[str, Any]:
    stamp = now_iso()
    if bestie_id:
        with connect() as conn:
            conn.execute(
                """
                UPDATE besties
                SET name = ?, personality = ?, avatar = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    data["name"],
                    data.get("personality") or "",
                    data.get("avatar") or "",
                    stamp,
                    bestie_id,
                ),
            )
        return get_bestie(bestie_id) or {}
    bestie_id = new_id()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO besties(id, name, personality, avatar, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (
                bestie_id,
                data["name"],
                data.get("personality") or "",
                data.get("avatar") or "",
                stamp,
                stamp,
            ),
        )
    return get_bestie(bestie_id) or {}


def delete_bestie(bestie_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM besties WHERE id = ?", (bestie_id,))


def create_conversation(
    title: str = "New chat",
    model: str = "",
    agent_id: str = "default",
    mode: str = "chat",
) -> Dict[str, Any]:
    stamp = now_iso()
    conversation_id = new_id()
    mode = "tool_builder" if mode == "tool_builder" else "chat"
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO conversations(id, title, model, agent_id, mode, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (conversation_id, title, model, agent_id, mode, stamp, stamp),
        )
    return get_conversation(conversation_id)["conversation"]


def update_conversation(conversation_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"title", "model", "agent_id", "pinned", "mode"}
    if "mode" in values:
        values = {**values, "mode": "tool_builder" if values["mode"] == "tool_builder" else "chat"}
    pairs = [(key, values[key]) for key in values if key in allowed]
    if not pairs:
        return get_conversation(conversation_id)["conversation"]
    assignments = ", ".join([f"{key} = ?" for key, _ in pairs])
    params = [value for _, value in pairs]
    params.extend([now_iso(), conversation_id])
    with connect() as conn:
        conn.execute(
            f"UPDATE conversations SET {assignments}, updated_at = ? WHERE id = ?",
            params,
        )
    return get_conversation(conversation_id)["conversation"]


def delete_conversation(conversation_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))


def list_conversations() -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT c.*,
                   (SELECT content FROM messages m WHERE m.conversation_id = c.id ORDER BY created_at DESC LIMIT 1) AS preview
            FROM conversations c
            ORDER BY pinned DESC, updated_at DESC
            """
        ).fetchall()
    return rows_to_dicts(rows)


def _parse_message_metadata(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_internal_assistant_message(role: str, metadata: Dict[str, Any]) -> bool:
    if role != "assistant":
        return False
    return bool(
        metadata.get("tool_call_round")
        or metadata.get("auto_continue")
        or metadata.get("progress_only")
    )


def _tool_event_from_message(message: Dict[str, Any]) -> Dict[str, Any]:
    metadata = message.get("metadata") or {}
    is_memory = metadata.get("tool_name") == "sammy_memory"
    return {
        "type": "memory" if is_memory else "result",
        "name": metadata.get("function_name") or metadata.get("tool_name") or "tool",
        "tool": metadata.get("tool_name") or "",
        "tool_display_name": metadata.get("tool_display_name") or "",
        "memory_file": metadata.get("memory_file") or "",
        "content": message.get("content") or "",
    }


def _append_history_trace(
    metadata: Dict[str, Any],
    progress_notes: List[str],
    tool_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    if not progress_notes and not tool_events:
        return metadata
    next_metadata = dict(metadata)
    if progress_notes:
        existing_notes = next_metadata.get("progress_notes")
        notes = existing_notes if isinstance(existing_notes, list) else []
        next_metadata["progress_notes"] = [*notes, *progress_notes][-12:]
    if tool_events:
        existing_events = next_metadata.get("tool_events")
        events = existing_events if isinstance(existing_events, list) else []
        next_metadata["tool_events"] = [*events, *tool_events]
    return next_metadata


def get_conversation(conversation_id: str) -> Dict[str, Any]:
    with connect() as conn:
        conversation = conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        messages = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    if conversation is None:
        raise KeyError(conversation_id)
    out_messages = []
    pending_progress_notes: List[str] = []
    pending_tool_events: List[Dict[str, Any]] = []
    for row in messages:
        item = dict(row)
        item["metadata"] = _parse_message_metadata(item.get("metadata"))
        role = str(item.get("role") or "")
        if role == "user":
            pending_progress_notes = []
            pending_tool_events = []
            out_messages.append(item)
            continue
        if _is_internal_assistant_message(role, item["metadata"]):
            content = str(item.get("content") or "").strip()
            if content:
                pending_progress_notes.append(content)
            continue
        if role == "tool":
            pending_tool_events.append(_tool_event_from_message(item))
            continue
        if role == "assistant":
            item["metadata"] = _append_history_trace(
                item["metadata"],
                pending_progress_notes,
                pending_tool_events,
            )
            pending_progress_notes = []
            pending_tool_events = []
        out_messages.append(item)
    return {"conversation": dict(conversation), "messages": out_messages}


def add_message(
    conversation_id: str,
    role: str,
    content: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stamp = now_iso()
    message_id = new_id()
    metadata = metadata or {}
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO messages(id, conversation_id, role, content, metadata, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (message_id, conversation_id, role, content, json.dumps(metadata), stamp),
        )
        conn.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (stamp, conversation_id),
        )
        if role == "user":
            maybe_title = content.strip().splitlines()[0][:56] or "New chat"
            conn.execute(
                """
                UPDATE conversations
                SET title = CASE WHEN title = 'New chat' THEN ? ELSE title END
                WHERE id = ?
                """,
                (maybe_title, conversation_id),
            )
    return {
        "id": message_id,
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "metadata": metadata,
        "created_at": stamp,
    }


def replace_messages_after(conversation_id: str, message_id: str) -> None:
    with connect() as conn:
        row = conn.execute(
            "SELECT created_at FROM messages WHERE id = ? AND conversation_id = ?",
            (message_id, conversation_id),
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM messages WHERE conversation_id = ? AND created_at > ?",
                (conversation_id, row["created_at"]),
            )


def list_chat_messages(conversation_id: str) -> List[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content, metadata FROM messages
            WHERE conversation_id = ? AND role IN ('user', 'assistant', 'tool')
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        ).fetchall()
    messages = []
    for row in rows:
        message = {"role": row["role"], "content": row["content"]}
        if row["role"] == "tool":
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            message["name"] = metadata.get("function_name") or metadata.get("tool_name")
        messages.append(message)
    return messages


def _normalize_memory(content: str) -> str:
    return re.sub(r"\s+", " ", str(content or "").strip()).casefold()


def _memory_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["sensitive"] = bool(item.get("sensitive"))
    return item


def add_memory(values: Dict[str, Any]) -> Dict[str, Any]:
    content = re.sub(r"\s+", " ", str(values.get("content") or "").strip())
    if not content:
        raise ValueError("Memory content cannot be empty")
    scope = str(values.get("scope") or "user")
    if scope not in {"soul", "user", "agent"}:
        raise ValueError("Invalid memory scope")
    agent_id = str(values.get("agent_id") or "") if scope == "agent" else ""
    normalized = _normalize_memory(content)
    stamp = now_iso()
    with connect() as conn:
        existing = conn.execute(
            """
            SELECT id FROM memories
            WHERE scope = ? AND agent_id = ? AND normalized_content = ?
              AND status IN ('active', 'pending')
            LIMIT 1
            """,
            (scope, agent_id, normalized),
        ).fetchone()
        if existing:
            return get_memory(existing["id"])
        memory_id = new_id()
        conn.execute(
            """
            INSERT INTO memories(
                id, scope, agent_id, kind, content, normalized_content, status,
                confidence, sensitive, source_conversation_id, source_message_id,
                source_label, expires_at, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                memory_id,
                scope,
                agent_id,
                str(values.get("kind") or "fact"),
                content,
                normalized,
                str(values.get("status") or "active"),
                max(0.0, min(1.0, float(values.get("confidence", 1.0)))),
                int(bool(values.get("sensitive", False))),
                values.get("source_conversation_id"),
                values.get("source_message_id"),
                str(values.get("source_label") or ""),
                values.get("expires_at"),
                stamp,
                stamp,
            ),
        )
    return get_memory(memory_id)


def get_memory(memory_id: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT m.*, c.title AS source_conversation_title
            FROM memories m
            LEFT JOIN conversations c ON c.id = m.source_conversation_id
            WHERE m.id = ?
            """,
            (memory_id,),
        ).fetchone()
    if row is None:
        raise KeyError(memory_id)
    return _memory_row(row)


def list_memories(
    scope: Optional[str] = None,
    status: Optional[str] = None,
    agent_id: Optional[str] = None,
    limit: int = 250,
) -> List[Dict[str, Any]]:
    clauses: List[str] = []
    params: List[Any] = []
    if scope:
        clauses.append("m.scope = ?")
        params.append(scope)
    if status:
        clauses.append("m.status = ?")
        params.append(status)
    if agent_id is not None:
        clauses.append("(m.scope != 'agent' OR m.agent_id = ?)")
        params.append(agent_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(max(1, min(int(limit), 1000)))
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT m.*, c.title AS source_conversation_title
            FROM memories m
            LEFT JOIN conversations c ON c.id = m.source_conversation_id
            {where}
            ORDER BY CASE m.status WHEN 'pending' THEN 0 WHEN 'active' THEN 1 ELSE 2 END,
                     m.updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    return [_memory_row(row) for row in rows]


def update_memory(memory_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"kind", "content", "status", "confidence", "sensitive", "expires_at"}
    updates = {key: value for key, value in values.items() if key in allowed}
    if "content" in updates:
        content = re.sub(r"\s+", " ", str(updates["content"] or "").strip())
        if not content:
            raise ValueError("Memory content cannot be empty")
        updates["content"] = content
        updates["normalized_content"] = _normalize_memory(content)
    if "confidence" in updates:
        updates["confidence"] = max(0.0, min(1.0, float(updates["confidence"])))
    if "sensitive" in updates:
        updates["sensitive"] = int(bool(updates["sensitive"]))
    if not updates:
        return get_memory(memory_id)
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with connect() as conn:
        cursor = conn.execute(
            f"UPDATE memories SET {assignments} WHERE id = ?",
            [*updates.values(), memory_id],
        )
    if cursor.rowcount == 0:
        raise KeyError(memory_id)
    return get_memory(memory_id)


def delete_memory(memory_id: str) -> None:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    if cursor.rowcount == 0:
        raise KeyError(memory_id)


def mark_memories_used(memory_ids: Iterable[str]) -> None:
    ids = list(memory_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with connect() as conn:
        conn.execute(
            f"""
            UPDATE memories SET last_used_at = ?, use_count = use_count + 1
            WHERE id IN ({placeholders})
            """,
            [now_iso(), *ids],
        )


def memory_stats() -> Dict[str, int]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM memories GROUP BY status"
        ).fetchall()
    stats = {"active": 0, "pending": 0, "archived": 0}
    stats.update({row["status"]: row["count"] for row in rows})
    return stats


def expire_memories() -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE memories SET status = 'archived', updated_at = ?
            WHERE status != 'archived' AND expires_at IS NOT NULL AND expires_at <= ?
            """,
            (now_iso(), now_iso()),
        )
    return cursor.rowcount


def search_messages(
    query: str,
    limit: int = 5,
    exclude_conversation_id: str = "",
) -> List[Dict[str, Any]]:
    stop_words = {
        "about", "again", "also", "and", "are", "can", "could", "for", "from", "have", "how",
        "into", "just", "please", "show", "that", "the", "this", "was", "what", "when", "where",
        "which", "with", "would", "you", "your",
    }
    tokens = [
        token for token in re.findall(r"[\w-]+", str(query or "").casefold(), flags=re.UNICODE)
        if len(token) > 2 and token not in stop_words
    ][:10]
    if not tokens:
        return []
    limit = max(1, min(int(limit), 20))
    exclude_sql = "AND m.conversation_id != ?" if exclude_conversation_id else ""
    exclude_params: List[Any] = [exclude_conversation_id] if exclude_conversation_id else []
    match_query = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
    with connect() as conn:
        try:
            rows = conn.execute(
                f"""
                SELECT m.id, m.conversation_id, m.role, m.content, m.created_at,
                       c.title AS conversation_title, bm25(messages_fts) AS rank
                FROM messages_fts
                JOIN messages m ON m.id = messages_fts.message_id
                JOIN conversations c ON c.id = m.conversation_id
                WHERE messages_fts MATCH ? AND m.role IN ('user', 'assistant') {exclude_sql}
                ORDER BY rank ASC, m.created_at DESC
                LIMIT ?
                """,
                [match_query, *exclude_params, limit],
            ).fetchall()
        except sqlite3.OperationalError:
            like_clauses = " OR ".join("m.content LIKE ?" for _ in tokens)
            rows = conn.execute(
                f"""
                SELECT m.id, m.conversation_id, m.role, m.content, m.created_at,
                       c.title AS conversation_title, 0 AS rank
                FROM messages m JOIN conversations c ON c.id = m.conversation_id
                WHERE ({like_clauses}) AND m.role IN ('user', 'assistant') {exclude_sql}
                ORDER BY m.created_at DESC LIMIT ?
                """,
                [*(f"%{token}%" for token in tokens), *exclude_params, limit],
            ).fetchall()
    return rows_to_dicts(rows)


def _plugin_proposal_row(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    try:
        item["capabilities"] = json.loads(item.get("capabilities") or "[]")
    except json.JSONDecodeError:
        item["capabilities"] = []
    item["allow_private_network"] = bool(item.get("allow_private_network"))
    item["write_access"] = bool(item.get("write_access"))
    return item


def create_plugin_proposal(values: Dict[str, Any]) -> Dict[str, Any]:
    stamp = now_iso()
    proposal_id = new_id()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO plugin_proposals(
                id, conversation_id, agent_id, source_user_message_id, service_name, goal, capabilities,
                base_url, documentation_url, auth_type, write_access, allow_private_network,
                status, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                proposal_id,
                values["conversation_id"],
                values["agent_id"],
                str(values.get("source_user_message_id") or ""),
                str(values.get("service_name") or "").strip(),
                str(values.get("goal") or "").strip(),
                json.dumps(values.get("capabilities") or []),
                str(values.get("base_url") or "").strip(),
                str(values.get("documentation_url") or "").strip(),
                str(values.get("auth_type") or "none").strip(),
                int(bool(values.get("write_access", False))),
                int(bool(values.get("allow_private_network", False))),
                stamp,
                stamp,
            ),
        )
    return get_plugin_proposal(proposal_id)


def latest_user_message_id(conversation_id: str) -> str:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id FROM messages
            WHERE conversation_id = ? AND role = 'user'
            ORDER BY created_at DESC LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
    return str(row["id"]) if row else ""


def get_plugin_proposal(proposal_id: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM plugin_proposals WHERE id = ?", (proposal_id,)).fetchone()
    if row is None:
        raise KeyError(proposal_id)
    return _plugin_proposal_row(row)


def latest_plugin_proposal(conversation_id: str, status: Optional[str] = None) -> Optional[Dict[str, Any]]:
    params: List[Any] = [conversation_id]
    status_sql = ""
    if status:
        status_sql = "AND status = ?"
        params.append(status)
    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM plugin_proposals
            WHERE conversation_id = ? {status_sql}
            ORDER BY updated_at DESC LIMIT 1
            """,
            params,
        ).fetchone()
    return _plugin_proposal_row(row) if row else None


def update_plugin_proposal(proposal_id: str, values: Dict[str, Any]) -> Dict[str, Any]:
    allowed = {"status", "plugin_name"}
    updates = {key: values[key] for key in values if key in allowed}
    if not updates:
        return get_plugin_proposal(proposal_id)
    updates["updated_at"] = now_iso()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with connect() as conn:
        cursor = conn.execute(
            f"UPDATE plugin_proposals SET {assignments} WHERE id = ?",
            [*updates.values(), proposal_id],
        )
    if cursor.rowcount == 0:
        raise KeyError(proposal_id)
    return get_plugin_proposal(proposal_id)


def get_tool_credentials(tool_name: str) -> Dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            "SELECT encrypted_credentials FROM tool_credentials WHERE tool_name = ?",
            (tool_name,),
        ).fetchone()
    if not row:
        return {}
    return decrypt_json(row["encrypted_credentials"])


def get_tool_statuses() -> Dict[str, bool]:
    with connect() as conn:
        rows = conn.execute("SELECT tool_name, connected FROM tool_credentials").fetchall()
    return {row["tool_name"]: bool(row["connected"]) for row in rows}


def save_tool_credentials(tool_name: str, credentials: Dict[str, Any], connected: bool) -> None:
    stamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tool_credentials(tool_name, encrypted_credentials, connected, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(tool_name) DO UPDATE SET
                encrypted_credentials = excluded.encrypted_credentials,
                connected = excluded.connected,
                updated_at = excluded.updated_at
            """,
            (tool_name, encrypt_json(credentials), int(connected), stamp),
        )


def save_upload(upload_id: str, filename: str, path: str, content_type: str, size: int) -> Dict[str, Any]:
    stamp = now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO uploads(id, filename, path, content_type, size, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (upload_id, filename, path, content_type, size, stamp),
        )
    return {
        "id": upload_id,
        "filename": filename,
        "path": path,
        "content_type": content_type,
        "size": size,
        "created_at": stamp,
    }


def get_upload(upload_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    return dict(row) if row else None


def save_oauth_state(state: str, tool_name: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO oauth_states(state, tool_name, created_at) VALUES(?, ?, ?)",
            (state, tool_name, now_iso()),
        )


def pop_oauth_state(state: str) -> Optional[str]:
    with connect() as conn:
        row = conn.execute("SELECT tool_name FROM oauth_states WHERE state = ?", (state,)).fetchone()
        conn.execute("DELETE FROM oauth_states WHERE state = ?", (state,))
    return row["tool_name"] if row else None
