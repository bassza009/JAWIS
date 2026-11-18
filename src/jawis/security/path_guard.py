"""Canonical filesystem boundary guard with Windows junction, DOS device, and secret checks."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from jawis.config import (
    HARD_DENIED_DIR_NAMES,
    HARD_DENIED_FILENAME_PATTERNS,
    AppConfig,
)
from jawis.security.audit import AuditLogger
from jawis.storage.db import Database

_RESERVED_DOS_NAMES = frozenset(
    {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
)


class PathAccessDeniedError(PermissionError):
    """Raised when a file or folder access violates security policy."""


class PathSecurityGuard:
    """Enforces allowlisted read-only directory boundaries and blocks secret/OS paths."""

    def __init__(self, config: AppConfig, db: Database, audit: AuditLogger) -> None:
        """Initialize canonical blocked roots from config and Windows environment."""
        self.config = config
        self._db = db
        self._audit = audit
        self._data_dir = self.normalize_long_path(config.data_dir.resolve())
        self._system_roots = self._collect_system_roots()
        self._exact_blocked_roots = self._collect_exact_blocked_roots()

    @staticmethod
    def normalize_long_path(path: Path) -> Path:
        """Strip Win32 extended-length prefix while preserving UNC long paths."""
        raw = str(path)
        if raw.startswith("\\\\?\\") and not raw.upper().startswith("\\\\?\\UNC\\"):
            return Path(raw[4:])
        return path

    @staticmethod
    def is_reparse_point(path: Path) -> bool:
        """Return True if path is a symlink or Windows directory junction."""
        try:
            if path.is_symlink():
                return True
        except OSError:
            return True
        is_junction = getattr(os.path, "isjunction", None)
        if is_junction is not None:
            try:
                return bool(is_junction(path))
            except OSError:
                return True
        return False

    @staticmethod
    def _resolve_normalized(raw_path: str) -> Path:
        # Ensure bare drive letters like "C:" resolve to "C:\\" instead of the per-drive CWD.
        if len(raw_path) == 2 and raw_path[1] == ":" and raw_path[0].isalpha():
            raw_path = raw_path + "\\"
        return PathSecurityGuard.normalize_long_path(Path(raw_path).resolve())

    @staticmethod
    def _collect_system_roots() -> tuple[Path, ...]:
        candidates = [
            os.environ.get("SystemRoot", r"C:\Windows"),
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("ProgramData", r"C:\ProgramData"),
        ]
        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            la = Path(local_app)
            candidates.extend(
                [
                    str(la / "JAWIS"),
                    str(la / "Google" / "Chrome" / "User Data"),
                    str(la / "Microsoft" / "Edge" / "User Data"),
                    str(la / "BraveSoftware" / "Brave-Browser" / "User Data"),
                    str(la / "Microsoft"),
                ]
            )
        roaming = os.environ.get("APPDATA")
        if roaming:
            ra = Path(roaming)
            candidates.extend(
                [
                    str(ra / "Microsoft"),
                    str(ra / "Mozilla" / "Firefox"),
                ]
            )
        roots: list[Path] = []
        for item in candidates:
            if item:
                try:
                    roots.append(PathSecurityGuard._resolve_normalized(item))
                except OSError:
                    continue
        return tuple(roots)

    @staticmethod
    def _collect_exact_blocked_roots() -> tuple[Path, ...]:
        roots: list[Path] = []
        for env_key in ("USERPROFILE", "SystemDrive"):
            val = os.environ.get(env_key)
            if val:
                try:
                    roots.append(PathSecurityGuard._resolve_normalized(val))
                except OSError:
                    continue
        sys_drive = os.environ.get("SystemDrive", "C:").rstrip("\\")
        try:
            roots.append(PathSecurityGuard._resolve_normalized(f"{sys_drive}\\Users"))
        except OSError:
            pass
        return tuple(roots)

    @staticmethod
    def _has_ntfs_ads_or_unc(raw_path: str) -> bool:
        stripped = raw_path.strip()
        if stripped.startswith("\\\\") or stripped.startswith("//"):
            return True
        colon_count = stripped.count(":")
        if colon_count == 0:
            return False
        if colon_count == 1 and len(stripped) >= 2 and stripped[1] == ":" and stripped[0].isalpha():
            return False
        return True

    @staticmethod
    def _has_reserved_dos_name(path: Path) -> bool:
        for part in path.parts:
            # Win32 strips trailing spaces/dots before matching reserved DOS device names.
            cleaned = part.rstrip(" .")
            stem = cleaned.split(".", 1)[0].rstrip(" ").lower()
            if stem in _RESERVED_DOS_NAMES:
                return True
        return False

    def is_sensitive_path(self, resolved: Path) -> bool:
        """Return True if path falls inside JAWIS data, OS/browser dirs, or hard-denied patterns."""
        norm = self.normalize_long_path(resolved)
        if norm == self._data_dir or norm.is_relative_to(self._data_dir):
            return True
        for sys_root in self._system_roots:
            if norm == sys_root or norm.is_relative_to(sys_root):
                return True
        for part in norm.parts:
            lowered = part.lower()
            if lowered in HARD_DENIED_DIR_NAMES:
                return True
            if any(fnmatch.fnmatch(lowered, pattern) for pattern in HARD_DENIED_FILENAME_PATTERNS):
                return True
        return False

    def _is_blocked_root(self, resolved: Path) -> bool:
        return resolved == Path(resolved.anchor) or resolved in self._exact_blocked_roots

    def _has_reparse_between(self, root: Path, candidate: Path) -> bool:
        lex = self.normalize_long_path(Path(os.path.abspath(candidate)))
        if not lex.is_relative_to(root):
            return False
        cur = lex
        while cur != root:
            if self.is_reparse_point(cur):
                return True
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
        return False

    def _deny(self, tool_name: str, raw_target: str, reason: str) -> PathAccessDeniedError:
        self._audit.record(
            event_type="PATH_ACCESS",
            actor="guard",
            tool_name=tool_name,
            target=raw_target,
            decision="BLOCKED",
            detail=reason,
        )
        return PathAccessDeniedError(reason)

    def _validate_and_resolve_candidate(self, raw_path: str, tool_name: str) -> tuple[Path, Path]:
        if (
            not raw_path
            or "\x00" in raw_path
            or self._has_ntfs_ads_or_unc(raw_path)
            or self._has_reserved_dos_name(Path(raw_path))
        ):
            raise self._deny(tool_name, raw_path, "UNC, ADS, empty, or reserved device path rejected")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            raise self._deny(tool_name, raw_path, "Relative path rejected")
        if self.is_reparse_point(candidate) or self.is_reparse_point(Path(os.path.abspath(candidate))):
            raise self._deny(tool_name, raw_path, "Symlink or directory junction rejected")
        try:
            resolved = self.normalize_long_path(candidate.resolve(strict=True))
        except OSError as exc:
            raise self._deny(tool_name, raw_path, f"Path cannot be resolved: {exc}") from exc
        return candidate, resolved

    def validate_folder_root(self, raw_path: str) -> Path:
        """Validate and canonicalize a candidate directory before adding to the allowlist."""
        _, resolved = self._validate_and_resolve_candidate(raw_path, "add_folder")
        if not resolved.is_dir():
            raise self._deny("add_folder", raw_path, "Target is not a directory")
        if self._is_blocked_root(resolved):
            raise self._deny("add_folder", raw_path, "Drive root or broad user-profile root cannot be allowlisted")
        if self.is_sensitive_path(resolved):
            raise self._deny("add_folder", raw_path, "System or JAWIS internal directory cannot be allowlisted")
        return resolved

    def validate_read_path(self, raw_path: str, tool_name: str = "read_allowed_file") -> Path:
        """Validate that raw_path canonically resolves inside an enabled allowlisted folder."""
        candidate, resolved = self._validate_and_resolve_candidate(raw_path, tool_name)
        if self.is_sensitive_path(resolved):
            raise self._deny(tool_name, str(resolved), "Blocked sensitive file or protected system path")

        for folder in self._db.list_folders(only_enabled=True):
            raw_root = Path(folder.root_path)
            if self.is_reparse_point(raw_root):
                continue
            try:
                root = self.normalize_long_path(raw_root.resolve(strict=True))
            except OSError:
                continue
            if self._is_blocked_root(root) or self.is_sensitive_path(root):
                continue
            if self._has_reparse_between(root, candidate):
                continue
            if resolved == root or resolved.is_relative_to(root):
                return resolved

        raise self._deny(tool_name, str(resolved), "Path is outside all enabled allowlisted folders")
