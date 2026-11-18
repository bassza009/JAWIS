"""Bounded 6-tool schema definitions and non-blocking security-gated async dispatcher."""

from __future__ import annotations

import asyncio
import inspect
from typing import Awaitable, Callable

from jawis.security.app_guard import AppAccessDeniedError, AppAllowlistGuard
from jawis.security.audit import AuditLogger
from jawis.security.confirmation import ConfirmationManager
from jawis.security.path_guard import PathAccessDeniedError
from jawis.storage.db import Database
from jawis.tools.app_controller import AppController
from jawis.tools.doc_reader import ScopedDocumentReader
from jawis.tools.telemetry import SystemTelemetry

TOOL_DEFINITIONS: list[dict[str, object]] = [
    {
        "name": "list_allowed_apps_and_status",
        "description": "List approved applications in the allowlist and whether each is currently running.",
        "parameters": None,
    },
    {
        "name": "open_app",
        "description": "Launch an approved application from the allowlist immediately.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {"type": "string", "description": "App key, display name, or alias (e.g. vscode, chrome)."}
            },
            "required": ["app_id"],
        },
    },
    {
        "name": "request_close_app",
        "description": "Request closing a running approved application. Always pauses for explicit user confirmation.",
        "parameters": {
            "type": "object",
            "properties": {
                "app_id": {"type": "string", "description": "App key, display name, or alias to close."}
            },
            "required": ["app_id"],
        },
    },
    {
        "name": "search_allowed_files",
        "description": "Search filenames inside approved read-only folders.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Case-insensitive filename substring (empty lists files)."},
                "folder_id": {"type": "integer", "description": "Optional specific allowlisted folder ID."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_allowed_file",
        "description": "Read bounded text from an approved file (.txt, .md, .csv, .json, code, .pdf, .docx, .xlsx).",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Full path inside an approved folder."},
                "page_or_section": {
                    "type": "string",
                    "description": "Optional PDF page/range (e.g. '1-5') or Excel sheet name.",
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "get_system_telemetry",
        "description": "Read laptop CPU, RAM, battery, and running status of approved apps.",
        "parameters": None,
    },
]


def _str_arg(args: dict[str, object], key: str) -> str:
    val = args.get(key)
    return "" if val is None else str(val)


class ToolRegistry:
    """Async security-gated dispatcher exposing only the 6 bounded J.A.R.V.I.S. tools."""

    def __init__(
        self,
        db: Database,
        app_guard: AppAllowlistGuard,
        app_ctrl: AppController,
        doc_reader: ScopedDocumentReader,
        telemetry: SystemTelemetry,
        confirm_mgr: ConfirmationManager,
        audit: AuditLogger,
    ) -> None:
        self._db = db
        self._app_guard = app_guard
        self._app_ctrl = app_ctrl
        self._doc_reader = doc_reader
        self._telemetry = telemetry
        self._confirm_mgr = confirm_mgr
        self._audit = audit

    @staticmethod
    def definitions() -> list[dict[str, object]]:
        """Return Gemini-compatible tool schemas for the 6 bounded tools."""
        return TOOL_DEFINITIONS

    async def _list_apps_and_status(self) -> dict[str, object]:
        apps = self._db.list_apps(only_enabled=True)
        rows = await asyncio.to_thread(
            lambda: [
                {
                    "app_key": a.app_key,
                    "display_name": a.display_name,
                    "aliases": a.aliases,
                    "running_pids": [t.pid for t in self._app_ctrl.running_process_snapshots(a)],
                }
                for a in apps
            ]
        )
        return {"apps": rows}

    async def _open_app(self, args: dict[str, object]) -> dict[str, object]:
        app_id = _str_arg(args, "app_id")
        try:
            app = self._app_guard.resolve_app(app_id)
        except AppAccessDeniedError as exc:
            return {"error": "APP_ACCESS_DENIED", "detail": str(exc)}

        try:
            pid = await asyncio.to_thread(self._app_ctrl.launch_app, app, self._app_guard)
            self._audit.record("APP_OPEN", "ai", "open_app", app.display_name, "ALLOWED", f"pid={pid}")
            return {"status": "opened", "app_key": app.app_key, "display_name": app.display_name, "pid": pid}
        except AppAccessDeniedError as exc:
            return {"error": "APP_ACCESS_DENIED", "detail": str(exc)}
        except OSError as exc:
            self._audit.record("APP_OPEN", "ai", "open_app", app.display_name, "ERROR", str(exc))
            return {"error": "APP_ACCESS_DENIED", "detail": str(exc)}

    async def _request_close_app(
        self,
        args: dict[str, object],
        on_confirm_request: Callable[[dict[str, object]], Awaitable[None] | None] | None,
    ) -> dict[str, object]:
        app_id = _str_arg(args, "app_id")
        try:
            app = self._app_guard.resolve_app(app_id)
        except AppAccessDeniedError as exc:
            return {"error": "APP_ACCESS_DENIED", "detail": str(exc)}

        snaps = await asyncio.to_thread(self._app_ctrl.running_process_snapshots, app)
        if not snaps:
            return {"status": "not_running", "app_key": app.app_key, "display_name": app.display_name}

        req = self._confirm_mgr.create_request(
            action="close_app",
            app_id=app.id,
            app_key=app.app_key,
            display_name=app.display_name,
            target_processes=snaps,
        )
        if on_confirm_request:
            maybe_awaitable = on_confirm_request(
                {
                    "confirm_id": req.confirm_id,
                    "action": req.action,
                    "app_key": req.app_key,
                    "display_name": req.display_name,
                    "pid_count": len(req.target_processes),
                    "timeout_seconds": self._confirm_mgr.timeout_seconds,
                }
            )
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable

        decision = await self._confirm_mgr.wait_for_decision(req.confirm_id)
        if decision.decision == "approve":
            res = await asyncio.to_thread(self._app_ctrl.close_processes_gracefully, req.target_processes)
            self._audit.record(
                "APP_CLOSE",
                "user",
                "request_close_app",
                app.display_name,
                "CONFIRMED",
                f"source={decision.source}; closed={res['closed']}; pending_save={res['pending_save']}",
            )
            status = "pending_save_dialog" if res["pending_save"] and not res["closed"] else "closed"
            return {"status": status, "display_name": app.display_name, **res}

        audit_decision = "TIMEOUT_CANCELLED" if decision.decision == "timeout" else "CANCELLED"
        self._audit.record(
            "APP_CLOSE",
            "user",
            "request_close_app",
            app.display_name,
            audit_decision,
            f"source={decision.source}",
        )
        return {"status": decision.decision, "display_name": app.display_name}

    async def _search_files(self, args: dict[str, object]) -> dict[str, object]:
        query = _str_arg(args, "query")
        raw_fid = args.get("folder_id")
        fid = raw_fid if isinstance(raw_fid, int) and not isinstance(raw_fid, bool) else None
        files = await asyncio.to_thread(self._doc_reader.search_files, query, fid)
        return {"files": files}

    async def _read_file(
        self,
        args: dict[str, object],
        on_file_access: Callable[[str], None] | None,
    ) -> dict[str, object]:
        file_path = _str_arg(args, "file_path")
        section = args.get("page_or_section")
        try:
            return await asyncio.to_thread(
                self._doc_reader.read_file,
                file_path,
                str(section) if section is not None else None,
                on_file_access,
            )
        except PathAccessDeniedError as exc:
            return {"error": "PATH_ACCESS_DENIED", "detail": str(exc)}

    async def execute(
        self,
        tool_name: str,
        args: dict[str, object] | None,
        *,
        on_file_access: Callable[[str], None] | None = None,
        on_confirm_request: Callable[[dict[str, object]], Awaitable[None] | None] | None = None,
    ) -> dict[str, object]:
        """Dispatch an allowlisted tool call off the event loop and block unknown tool names."""
        safe_args = args or {}
        if tool_name == "list_allowed_apps_and_status":
            return await self._list_apps_and_status()
        if tool_name == "open_app":
            return await self._open_app(safe_args)
        if tool_name == "request_close_app":
            return await self._request_close_app(safe_args, on_confirm_request)
        if tool_name == "search_allowed_files":
            return await self._search_files(safe_args)
        if tool_name == "read_allowed_file":
            return await self._read_file(safe_args, on_file_access)
        if tool_name == "get_system_telemetry":
            return await asyncio.to_thread(self._telemetry.snapshot, self._db.list_apps(only_enabled=True))

        self._audit.record("TOOL_CALL", "ai", tool_name, str(safe_args), "BLOCKED", "Unknown tool name")
        return {"error": "UNKNOWN_TOOL", "tool_name": tool_name}
