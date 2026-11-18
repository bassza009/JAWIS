"""Append-only security audit logging wrapper."""

from __future__ import annotations

from jawis.storage.db import Database


class AuditLogger:
    """Writes immutable security audit events to the local SQLite audit log."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self,
        event_type: str,
        actor: str,
        tool_name: str,
        target: str,
        decision: str,
        detail: str = "",
    ) -> int:
        """Append a single audit event row and return its row ID."""
        return self._db.append_audit(
            event_type=event_type,
            actor=actor,
            tool_name=tool_name,
            target=target,
            decision=decision,
            detail=detail,
        )
