"""Deterministic allowlist guard resolving .exe targets first and blocking ambiguous aliases."""

from __future__ import annotations

from pathlib import Path
import re

from jawis.config import BLOCKED_SYSTEM_EXECUTABLES
from jawis.security.audit import AuditLogger
from jawis.storage.db import AllowedApp, Database

_UNSAFE_ARG_RE = re.compile(r"[&|;><`\r\n\x00]")


class AppAccessDeniedError(PermissionError):
    """Raised when an application is unapproved, ambiguous, or points to a blocked binary."""


class AppAllowlistGuard:
    """Enforces .exe-only registration, LOLBIN rejection, and unambiguous alias resolution."""

    def __init__(self, db: Database, audit: AuditLogger) -> None:
        self._db = db
        self._audit = audit

    def _deny(
        self,
        event_type: str,
        actor: str,
        tool_name: str,
        target: str,
        reason: str,
    ) -> AppAccessDeniedError:
        self._audit.record(
            event_type=event_type,
            actor=actor,
            tool_name=tool_name,
            target=target,
            decision="BLOCKED",
            detail=reason,
        )
        return AppAccessDeniedError(reason)

    def validate_registration(
        self,
        executable_path: str,
        process_names: list[str],
        launch_args: list[str] | None = None,
    ) -> Path:
        """Validate a candidate app registration against .exe, LOLBIN, process, and arg rules."""
        stripped_exe = executable_path.strip()
        is_unc = stripped_exe.startswith(("\\\\", "//")) and not (
            stripped_exe.startswith("\\\\?\\") and not stripped_exe.upper().startswith("\\\\?\\UNC\\")
        )
        if not stripped_exe or "\x00" in executable_path or is_unc:
            raise self._deny(
                "APP_REGISTER",
                "user",
                "register_app",
                executable_path,
                "Empty, UNC, or invalid executable path",
            )
        try:
            resolved = Path(executable_path).resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise self._deny(
                "APP_REGISTER",
                "user",
                "register_app",
                executable_path,
                f"Executable not found: {executable_path}",
            ) from exc

        if not resolved.is_file() or resolved.suffix.lower() != ".exe":
            raise self._deny(
                "APP_REGISTER",
                "user",
                "register_app",
                str(resolved),
                "Only .exe files are permitted",
            )

        exe_name = resolved.name.lower()
        if exe_name in BLOCKED_SYSTEM_EXECUTABLES:
            raise self._deny(
                "APP_REGISTER",
                "user",
                "register_app",
                str(resolved),
                f"Blocked system executable: {resolved.name}",
            )

        if not process_names:
            raise self._deny(
                "APP_REGISTER",
                "user",
                "register_app",
                str(resolved),
                "At least one process name is required",
            )
        for pname in process_names:
            clean = pname.strip().lower()
            if (
                not clean
                or "\x00" in clean
                or clean in BLOCKED_SYSTEM_EXECUTABLES
                or f"{clean}.exe" in BLOCKED_SYSTEM_EXECUTABLES
            ):
                raise self._deny(
                    "APP_REGISTER",
                    "user",
                    "register_app",
                    pname,
                    f"Blocked or empty process name: {pname}",
                )

        args = launch_args or []
        if len(args) > 10:
            raise self._deny(
                "APP_REGISTER",
                "user",
                "register_app",
                str(resolved),
                "Too many launch arguments (max 10)",
            )
        for arg in args:
            if not arg or not arg.strip() or len(arg) > 260 or _UNSAFE_ARG_RE.search(arg):
                raise self._deny(
                    "APP_REGISTER",
                    "user",
                    "register_app",
                    str(resolved),
                    f"Unsafe or malformed launch argument: {arg!r}",
                )

        return resolved

    def resolve_app(self, app_identifier: str) -> AllowedApp:
        """Resolve an app identifier unambiguously against enabled allowlisted applications."""
        needle = app_identifier.strip().lower()
        if not needle:
            raise self._deny("APP_ACCESS", "ai", "resolve_app", app_identifier, "Empty application identifier")

        matches: list[AllowedApp] = []
        for app in self._db.list_apps(only_enabled=True):
            candidates = {
                app.app_key.strip().lower(),
                app.display_name.strip().lower(),
                *(a.strip().lower() for a in app.aliases if a.strip()),
            }
            if needle in candidates:
                matches.append(app)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(m.display_name for m in matches)
            raise self._deny(
                "APP_ACCESS",
                "ai",
                "resolve_app",
                app_identifier,
                f"Ambiguous application '{app_identifier}' matched: {names}",
            )

        raise self._deny(
            "APP_ACCESS",
            "ai",
            "resolve_app",
            app_identifier,
            "Application not in enabled allowlist",
        )

    def all_allowed_process_names(self) -> set[str]:
        """Return all lowercased process names belonging to enabled allowlisted apps."""
        names: set[str] = set()
        for app in self._db.list_apps(only_enabled=True):
            names.update(p.strip().lower() for p in app.process_names if p.strip())
        return names
