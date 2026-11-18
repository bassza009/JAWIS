"""Thread-safe WAL-mode SQLite persistence with bounded retention and clean shutdown."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class AllowedApp:
    id: int
    app_key: str
    display_name: str
    aliases: list[str]
    executable_path: str
    launch_args: list[str]
    process_names: list[str]
    enabled: bool


@dataclass(frozen=True)
class AllowedFolder:
    id: int
    root_path: str
    label: str
    enabled: bool


@dataclass(frozen=True)
class AuditEntry:
    id: int
    timestamp: str
    event_type: str
    actor: str
    tool_name: str
    target: str
    decision: str
    detail: str


@dataclass(frozen=True)
class TranscriptEntry:
    id: int
    timestamp: str
    role: str
    content: str
    meta_json: str


class Database:
    """Thread-safe SQLite store guarding against post-close queries during shutdown."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_allowlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_key TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    executable_path TEXT NOT NULL,
                    launch_args_json TEXT NOT NULL,
                    process_names_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS folder_allowlist (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    root_path TEXT NOT NULL UNIQUE,
                    label TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    target TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    detail TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS transcripts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    meta_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def add_app(
        self,
        *,
        app_key: str,
        display_name: str,
        aliases: list[str],
        executable_path: str,
        launch_args: list[str],
        process_names: list[str],
        enabled: bool = True,
    ) -> int:
        with self._lock:
            if self._closed:
                return 0
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO app_allowlist (
                        app_key, display_name, aliases_json, executable_path,
                        launch_args_json, process_names_json, enabled
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        app_key.strip().lower(),
                        display_name.strip(),
                        json.dumps(aliases, ensure_ascii=False),
                        executable_path,
                        json.dumps(launch_args, ensure_ascii=False),
                        json.dumps([p.lower() for p in process_names], ensure_ascii=False),
                        1 if enabled else 0,
                    ),
                )
                return int(cur.lastrowid or 0)

    def list_apps(self, *, only_enabled: bool = False) -> list[AllowedApp]:
        query = "SELECT * FROM app_allowlist"
        if only_enabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY display_name ASC"
        with self._lock:
            if self._closed:
                return []
            rows = self._conn.execute(query).fetchall()
        return [
            AllowedApp(
                id=row["id"],
                app_key=row["app_key"],
                display_name=row["display_name"],
                aliases=json.loads(row["aliases_json"]),
                executable_path=row["executable_path"],
                launch_args=json.loads(row["launch_args_json"]),
                process_names=json.loads(row["process_names_json"]),
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]

    def set_app_enabled(self, app_id: int, enabled: bool) -> None:
        with self._lock:
            if self._closed:
                return
            with self._conn:
                self._conn.execute(
                    "UPDATE app_allowlist SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, app_id),
                )

    def delete_app(self, app_id: int) -> None:
        with self._lock:
            if self._closed:
                return
            with self._conn:
                self._conn.execute("DELETE FROM app_allowlist WHERE id = ?", (app_id,))

    def add_folder(self, root_path: str, label: str, enabled: bool = True) -> int:
        with self._lock:
            if self._closed:
                return 0
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO folder_allowlist (root_path, label, enabled) VALUES (?, ?, ?)",
                    (str(Path(root_path).resolve()), label.strip(), 1 if enabled else 0),
                )
                return int(cur.lastrowid or 0)

    def list_folders(self, *, only_enabled: bool = False) -> list[AllowedFolder]:
        query = "SELECT * FROM folder_allowlist"
        if only_enabled:
            query += " WHERE enabled = 1"
        query += " ORDER BY label ASC"
        with self._lock:
            if self._closed:
                return []
            rows = self._conn.execute(query).fetchall()
        return [
            AllowedFolder(
                id=row["id"],
                root_path=row["root_path"],
                label=row["label"],
                enabled=bool(row["enabled"]),
            )
            for row in rows
        ]

    def set_folder_enabled(self, folder_id: int, enabled: bool) -> None:
        with self._lock:
            if self._closed:
                return
            with self._conn:
                self._conn.execute(
                    "UPDATE folder_allowlist SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, folder_id),
                )

    def delete_folder(self, folder_id: int) -> None:
        with self._lock:
            if self._closed:
                return
            with self._conn:
                self._conn.execute("DELETE FROM folder_allowlist WHERE id = ?", (folder_id,))

    def append_audit(
        self,
        event_type: str,
        actor: str,
        tool_name: str,
        target: str,
        decision: str,
        detail: str = "",
    ) -> int:
        with self._lock:
            if self._closed:
                return 0
            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO audit_logs (
                        timestamp, event_type, actor, tool_name, target, decision, detail
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (_utc_now_iso(), event_type, actor, tool_name, target, decision, detail),
                )
                return int(cur.lastrowid or 0)

    def list_audits(self, *, limit: int = 100, decision: str | None = None) -> list[AuditEntry]:
        params: list[object] = []
        query = "SELECT * FROM audit_logs"
        if decision:
            query += " WHERE decision = ?"
            params.append(decision)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            if self._closed:
                return []
            rows = self._conn.execute(query, params).fetchall()
        return [
            AuditEntry(
                id=row["id"],
                timestamp=row["timestamp"],
                event_type=row["event_type"],
                actor=row["actor"],
                tool_name=row["tool_name"],
                target=row["target"],
                decision=row["decision"],
                detail=row["detail"],
            )
            for row in rows
        ]

    def append_transcript(self, role: str, content: str, meta_json: str = "{}") -> int:
        with self._lock:
            if self._closed:
                return 0
            with self._conn:
                cur = self._conn.execute(
                    "INSERT INTO transcripts (timestamp, role, content, meta_json) VALUES (?, ?, ?, ?)",
                    (_utc_now_iso(), role, content, meta_json),
                )
                return int(cur.lastrowid or 0)

    def list_transcripts(self, *, limit: int = 100) -> list[TranscriptEntry]:
        with self._lock:
            if self._closed:
                return []
            rows = self._conn.execute(
                "SELECT * FROM transcripts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            TranscriptEntry(
                id=row["id"],
                timestamp=row["timestamp"],
                role=row["role"],
                content=row["content"],
                meta_json=row["meta_json"],
            )
            for row in reversed(rows)
        ]

    def clear_transcripts(self) -> None:
        with self._lock:
            if self._closed:
                return
            with self._conn:
                self._conn.execute("DELETE FROM transcripts")

    def prune_old_records(self, *, max_audits: int = 5000, max_transcripts: int = 500) -> None:
        with self._lock:
            if self._closed:
                return
            with self._conn:
                self._conn.execute(
                    "DELETE FROM audit_logs WHERE id NOT IN (SELECT id FROM audit_logs ORDER BY id DESC LIMIT ?)",
                    (max_audits,),
                )
                self._conn.execute(
                    "DELETE FROM transcripts WHERE id NOT IN (SELECT id FROM transcripts ORDER BY id DESC LIMIT ?)",
                    (max_transcripts,),
                )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            if self._closed:
                return default
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            if self._closed:
                return
            with self._conn:
                self._conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except sqlite3.Error:
                pass
            self._conn.close()
            self._closed = True
