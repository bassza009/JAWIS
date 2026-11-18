"""Windows Start Menu COM/SHLLINK scanner, re-validated launcher, and safe 64-bit WM_CLOSE closer."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import shlex
import struct
import subprocess
import time

import psutil

from jawis.config import BLOCKED_SYSTEM_EXECUTABLES
from jawis.security.app_guard import AppAllowlistGuard
from jawis.security.confirmation import TargetProcess
from jawis.storage.db import AllowedApp

WM_CLOSE = 0x0010
GW_OWNER = 4

_user32 = getattr(ctypes, "windll", None)
if _user32 is not None:
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u32 = _user32.user32
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsWindowVisible.restype = wintypes.BOOL
    _u32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _u32.GetWindow.restype = wintypes.HWND
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _u32.PostMessageW.restype = wintypes.BOOL
    _u32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _u32.EnumWindows.restype = wintypes.BOOL


def _is_valid_exe_path(candidate: str) -> bool:
    if not candidate or not candidate.lower().endswith(".exe"):
        return False
    try:
        return Path(candidate).is_file()
    except OSError:
        return False


def _read_counted_string(data: bytes, offset: int, is_unicode: bool) -> tuple[str, int]:
    if offset + 2 > len(data):
        return "", len(data)
    char_count = struct.unpack_from("<H", data, offset)[0]
    offset += 2
    byte_count = char_count * (2 if is_unicode else 1)
    chunk = data[offset : offset + byte_count]
    text = chunk.decode("utf-16-le" if is_unicode else "mbcs", errors="ignore")
    return text, min(len(data), offset + byte_count)


def _extract_link_info_target(data: bytes, offset: int) -> tuple[str | None, int]:
    if offset + 28 > len(data):
        return None, len(data)
    link_info_size = struct.unpack_from("<I", data, offset)[0]
    if link_info_size < 28:
        return None, len(data)
    header_size = struct.unpack_from("<I", data, offset + 4)[0]
    info_flags = struct.unpack_from("<I", data, offset + 8)[0]
    block_end = min(len(data), offset + link_info_size)
    target: str | None = None

    if info_flags & 0x01:
        if header_size >= 0x24 and offset + 32 <= block_end:
            uni_off = struct.unpack_from("<I", data, offset + 28)[0]
            if uni_off > 0 and offset + uni_off < block_end:
                end = data.find(b"\x00\x00", offset + uni_off, block_end)
                if end != -1 and (end - (offset + uni_off)) % 2 != 0:
                    end += 1
                raw_str = data[offset + uni_off : end if end != -1 else block_end]
                cand = raw_str.decode("utf-16-le", errors="ignore").rstrip("\x00")
                if _is_valid_exe_path(cand):
                    target = cand
        if not target:
            base_off = struct.unpack_from("<I", data, offset + 16)[0]
            if base_off > 0 and offset + base_off < block_end:
                end = data.find(b"\x00", offset + base_off, block_end)
                cand = data[offset + base_off : end if end != -1 else block_end].decode(
                    "mbcs", errors="ignore"
                ).rstrip("\x00")
                if _is_valid_exe_path(cand):
                    target = cand

    return target, block_end


def _extract_lnk_via_binary(lnk_path: Path) -> tuple[str | None, list[str]]:
    """Fallback MS-SHLLINK binary parser when WScript.Shell COM is unavailable."""
    try:
        data = lnk_path.read_bytes()
    except OSError:
        return None, []
    if len(data) < 76 or data[:4] != b"\x4c\x00\x00\x00":
        return None, []

    flags = struct.unpack_from("<I", data, 0x14)[0]
    is_unicode = bool(flags & 0x80)
    offset = 0x4C
    target: str | None = None
    args: list[str] = []

    if flags & 0x01 and offset + 2 <= len(data):
        offset += 2 + struct.unpack_from("<H", data, offset)[0]
    if flags & 0x02:
        target, offset = _extract_link_info_target(data, offset)

    # Parse StringData sections in spec order: Name (0x04), RelativePath (0x08), WorkingDir (0x10), Arguments (0x20)
    if flags & 0x04:
        _, offset = _read_counted_string(data, offset, is_unicode)
    if flags & 0x08:
        rel, offset = _read_counted_string(data, offset, is_unicode)
        if not target and rel:
            try:
                cand_rel = (lnk_path.parent / rel).resolve()
                if cand_rel.suffix.lower() == ".exe" and cand_rel.is_file():
                    target = str(cand_rel)
            except OSError:
                pass
    if flags & 0x10:
        _, offset = _read_counted_string(data, offset, is_unicode)
    if flags & 0x20:
        raw_args, offset = _read_counted_string(data, offset, is_unicode)
        if raw_args:
            try:
                args = shlex.split(raw_args, posix=False)
            except ValueError:
                args = []

    return target, args


def _resolve_lnk_shortcut(lnk_path: Path) -> tuple[str | None, list[str]]:
    """Use Win32 WScript.Shell COM when available (resolves MSI/env shortcuts), else MS-SHLLINK parser."""
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            sh = win32com.client.Dispatch("WScript.Shell")
            sc = sh.CreateShortCut(str(lnk_path))
            target = os.path.expandvars(str(sc.TargetPath or "")).strip()
            raw_args = str(sc.Arguments or "").strip()
            if _is_valid_exe_path(target):
                parsed_args = shlex.split(raw_args, posix=False) if raw_args else []
                return target, parsed_args
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass
    return _extract_lnk_via_binary(lnk_path)


class AppController:
    """Executes allowlisted Windows application launch and non-destructive graceful shutdown."""

    @staticmethod
    def scan_start_menu(limit: int = 120) -> list[dict[str, object]]:
        """Scan Start Menu shortcuts into deduplicated allowlist candidates, excluding LOLBINs."""
        roots: list[Path] = []
        for env_key, default in (("ProgramData", r"C:\ProgramData"), ("APPDATA", "")):
            base = (os.environ.get(env_key) or default).strip()
            if base:
                roots.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")

        found: list[dict[str, object]] = []
        seen_keys: set[str] = set()
        seen_sig: set[tuple[str, tuple[str, ...]]] = set()

        for root in roots:
            try:
                if not root.exists():
                    continue
                lnk_files = sorted(
                    root.rglob("*.lnk"),
                    key=lambda p: (p.stem.lower(), str(p).lower()),
                )
            except OSError:
                continue

            for lnk in lnk_files:
                target, launch_args = _resolve_lnk_shortcut(lnk)
                if not target:
                    continue
                exe_name = Path(target).name.lower()
                sig = (target.lower(), tuple(launch_args))
                if exe_name in BLOCKED_SYSTEM_EXECUTABLES or sig in seen_sig:
                    continue
                seen_sig.add(sig)
                base_key = lnk.stem.lower().replace(" ", "_")
                app_key = base_key
                suffix = 2
                while app_key in seen_keys:
                    app_key = f"{base_key}_{suffix}"
                    suffix += 1
                seen_keys.add(app_key)
                found.append(
                    {
                        "display_name": lnk.stem,
                        "app_key": app_key,
                        "executable_path": target,
                        "launch_args": launch_args,
                        "process_name": exe_name,
                    }
                )

        found.sort(key=lambda x: str(x["display_name"]).lower())
        return found[:limit]

    @staticmethod
    def running_process_snapshots(app: AllowedApp) -> list[TargetProcess]:
        """Capture PID + create_time + name snapshots for running processes of an allowlisted app."""
        targets = {p.lower() for p in app.process_names}
        snaps: list[TargetProcess] = []
        self_pid = os.getpid()
        for proc in psutil.process_iter(["pid", "name", "create_time"]):
            try:
                pid = proc.info.get("pid")
                name = (proc.info.get("name") or "").lower()
                create_time = proc.info.get("create_time")
                if pid is None or create_time is None or int(pid) == self_pid:
                    continue
                if name in targets:
                    snaps.append(
                        TargetProcess(
                            pid=int(pid),
                            create_time=float(create_time),
                            process_name=name,
                        )
                    )
            except (psutil.NoSuchProcess, psutil.AccessDenied, TypeError, ValueError):
                continue
        return snaps

    @staticmethod
    def launch_app(app: AllowedApp, guard: AppAllowlistGuard) -> int:
        """Re-validate an allowlisted app and launch it detached with shell=False."""
        resolved = guard.validate_registration(app.executable_path, app.process_names, app.launch_args)
        proc = subprocess.Popen(
            [str(resolved), *app.launch_args],
            cwd=str(resolved.parent),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0),
        )
        return int(proc.pid)

    @staticmethod
    def _post_wm_close_to_pids(
        pids: set[int],
        targets_by_pid: dict[int, TargetProcess] | None = None,
    ) -> int:
        if _user32 is None or not pids:
            return 0
        posted = 0

        def callback(hwnd: int, _lparam: int) -> bool:
            nonlocal posted
            if not _u32.IsWindowVisible(hwnd) or _u32.GetWindow(hwnd, GW_OWNER):
                return True
            pid = wintypes.DWORD()
            _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                # Re-verify PID+create_time+name immediately before posting WM_CLOSE to prevent TOCTOU.
                target = targets_by_pid.get(pid.value) if targets_by_pid is not None else None
                if target is None or AppController._verify_live_target(target):
                    _u32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    posted += 1
            return True

        _u32.EnumWindows(_WNDENUMPROC(callback), 0)
        return posted

    @staticmethod
    def _verify_live_target(target: TargetProcess) -> bool:
        if target.pid == os.getpid():
            return False
        try:
            proc = psutil.Process(target.pid)
            proc_name = proc.name().lower()
            return (
                abs(proc.create_time() - target.create_time) < 0.5
                and proc_name == target.process_name.lower()
                and proc_name not in BLOCKED_SYSTEM_EXECUTABLES
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def close_processes_gracefully(
        self, targets: list[TargetProcess], wait_seconds: float = 3.5
    ) -> dict[str, list[int]]:
        """Send WM_CLOSE to visible top-level windows; never force-kill unsaved dialogs."""
        verified = [t for t in targets if self._verify_live_target(t)]
        if not verified:
            return {"closed": [], "pending_save": []}

        targets_by_pid = {t.pid: t for t in verified}
        self._post_wm_close_to_pids(set(targets_by_pid), targets_by_pid)
        deadline = time.monotonic() + wait_seconds
        remaining = list(verified)
        while remaining and time.monotonic() < deadline:
            remaining = [t for t in remaining if self._verify_live_target(t)]
            if remaining:
                time.sleep(0.15)

        remaining_pids = {t.pid for t in remaining}
        closed = [t.pid for t in verified if t.pid not in remaining_pids]
        return {"closed": closed, "pending_save": [t.pid for t in remaining]}
