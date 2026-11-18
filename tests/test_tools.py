import asyncio
import os
from pathlib import Path
import struct
from types import SimpleNamespace

from google.genai import types as genai_types
import psutil
import pytest

from jawis.config import load_config
from jawis.security.app_guard import AppAllowlistGuard
from jawis.security.audit import AuditLogger
from jawis.security.confirmation import ConfirmationManager, TargetProcess
from jawis.security.path_guard import PathSecurityGuard
from jawis.storage.db import Database
from jawis.tools.app_controller import AppController, _extract_lnk_via_binary
from jawis.tools.doc_reader import ScopedDocumentReader
from jawis.tools.registry import TOOL_DEFINITIONS, ToolRegistry
from jawis.tools.telemetry import SystemTelemetry


def _build_registry(
    tmp_path: Path, timeout_seconds: float = 0.3
) -> tuple[ToolRegistry, Database, AppController, ConfirmationManager, Path]:
    cfg = load_config(tmp_path / "data")
    db = Database(cfg.db_path)
    exe = tmp_path / "notepad.exe"
    exe.write_text("dummy", encoding="utf-8")
    db.add_app(
        app_key="notepad",
        display_name="Notepad",
        aliases=["notepad"],
        executable_path=str(exe),
        launch_args=[],
        process_names=["notepad.exe"],
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    db.add_folder(str(docs), "Docs")

    audit = AuditLogger(db)
    app_guard = AppAllowlistGuard(db, audit)
    path_guard = PathSecurityGuard(cfg, db, audit)
    confirm_mgr = ConfirmationManager(timeout_seconds=timeout_seconds)
    app_ctrl = AppController()
    doc_reader = ScopedDocumentReader(cfg, db, path_guard, audit)
    telemetry = SystemTelemetry()
    registry = ToolRegistry(db, app_guard, app_ctrl, doc_reader, telemetry, confirm_mgr, audit)
    return registry, db, app_ctrl, confirm_mgr, docs


def _build_unicode_lnk(lnk_path: Path, rel_target: str, args: str) -> None:
    """Construct a minimal valid MS-SHLLINK (.lnk) file with RelativePath and Arguments."""
    header = bytearray(76)
    struct.pack_into("<I", header, 0, 0x4C)
    # Flags: HasRelativePath (0x08) | HasArguments (0x20) | IsUnicode (0x80)
    struct.pack_into("<I", header, 0x14, 0x08 | 0x20 | 0x80)

    def counted_u16(text: str) -> bytes:
        return struct.pack("<H", len(text)) + text.encode("utf-16-le")

    lnk_path.write_bytes(bytes(header) + counted_u16(rel_target) + counted_u16(args))


@pytest.mark.asyncio
async def test_registry_executes_open_and_requires_confirmation_on_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_config(tmp_path / "data")
    db = Database(cfg.db_path)
    try:
        exe = tmp_path / "notepad.exe"
        exe.write_text("dummy", encoding="utf-8")
        db.add_app(
            app_key="notepad",
            display_name="Notepad",
            aliases=["notepad"],
            executable_path=str(exe),
            launch_args=[],
            process_names=["notepad.exe"],
        )
        audit = AuditLogger(db)
        app_guard = AppAllowlistGuard(db, audit)
        path_guard = PathSecurityGuard(cfg, db, audit)
        confirm_mgr = ConfirmationManager(timeout_seconds=0.3)
        app_ctrl = AppController()
        doc_reader = ScopedDocumentReader(cfg, db, path_guard, audit)
        telemetry = SystemTelemetry()

        monkeypatch.setattr(app_ctrl, "launch_app", lambda app, guard: 4321)
        snap = TargetProcess(pid=4321, create_time=111.0, process_name="notepad.exe")
        monkeypatch.setattr(app_ctrl, "running_process_snapshots", lambda app: [snap])
        closed: list[int] = []
        monkeypatch.setattr(
            app_ctrl,
            "close_processes_gracefully",
            lambda targets: closed.extend(t.pid for t in targets) or {"closed": [4321], "pending_save": []},
        )

        registry = ToolRegistry(db, app_guard, app_ctrl, doc_reader, telemetry, confirm_mgr, audit)
        open_res = await registry.execute("open_app", {"app_id": "notepad"})
        assert open_res["status"] == "opened"

        # Boolean masquerading as folder_id must be rejected cleanly
        search_res = await registry.execute("search_allowed_files", {"query": "", "folder_id": True})
        assert "files" in search_res

        assert (await registry.execute("run_shell", {"cmd": "dir"}))["error"] == "UNKNOWN_TOOL"

        events: list[dict] = []

        async def approve_later() -> None:
            await asyncio.sleep(0.03)
            req = confirm_mgr.active_request()
            assert req is not None
            confirm_mgr.resolve(req.confirm_id, "approve", source="hud")

        asyncio.create_task(approve_later())
        close_res = await registry.execute(
            "request_close_app",
            {"app_id": "notepad"},
            on_confirm_request=events.append,
        )
        assert close_res["status"] == "closed"
        assert closed == [4321]
        assert events[0]["app_key"] == "notepad"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_registry_audits_timeout_cancel_pending_save_and_reads_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, db, app_ctrl, confirm_mgr, docs = _build_registry(tmp_path, timeout_seconds=0.05)
    try:
        # Tool definitions must validate cleanly against Google GenAI SDK FunctionDeclaration
        assert len(TOOL_DEFINITIONS) == 6
        assert len(ToolRegistry.definitions()) == 6
        for defn in TOOL_DEFINITIONS:
            decl = genai_types.FunctionDeclaration(**defn)
            assert decl.name == defn["name"]

        # list_allowed_apps_and_status + not_running close
        monkeypatch.setattr(app_ctrl, "running_process_snapshots", lambda app: [])
        listed = await registry.execute("list_allowed_apps_and_status", {})
        assert listed["apps"][0]["app_key"] == "notepad"
        assert listed["apps"][0]["running_pids"] == []

        not_running = await registry.execute("request_close_app", {"app_id": "notepad"})
        assert not_running["status"] == "not_running"

        # Timeout path -> TIMEOUT_CANCELLED
        snap = TargetProcess(pid=7777, create_time=222.0, process_name="notepad.exe")
        monkeypatch.setattr(app_ctrl, "running_process_snapshots", lambda app: [snap])
        timeout_res = await registry.execute("request_close_app", {"app_id": "notepad"})
        assert timeout_res["status"] == "timeout"
        assert any(a.decision == "TIMEOUT_CANCELLED" for a in db.list_audits(decision="TIMEOUT_CANCELLED"))

        # Cancel path -> CANCELLED
        async def cancel_later() -> None:
            await asyncio.sleep(0.01)
            req = confirm_mgr.active_request()
            assert req is not None
            confirm_mgr.resolve(req.confirm_id, "cancel", source="voice")

        asyncio.create_task(cancel_later())
        cancel_res = await registry.execute("request_close_app", {"app_id": "notepad"})
        assert cancel_res["status"] == "cancel"
        assert any(a.decision == "CANCELLED" for a in db.list_audits(decision="CANCELLED"))

        # Approve with pending_save_dialog
        monkeypatch.setattr(
            app_ctrl,
            "close_processes_gracefully",
            lambda targets: {"closed": [], "pending_save": [7777]},
        )

        async def approve_later() -> None:
            await asyncio.sleep(0.01)
            req = confirm_mgr.active_request()
            assert req is not None
            confirm_mgr.resolve(req.confirm_id, "approve", source="hud")

        asyncio.create_task(approve_later())
        pending_res = await registry.execute("request_close_app", {"app_id": "notepad"})
        assert pending_res["status"] == "pending_save_dialog"
        assert any(a.decision == "CONFIRMED" for a in db.list_audits(decision="CONFIRMED"))

        # read_allowed_file success and denial
        sample = docs / "readme.txt"
        sample.write_text("hello jarvis", encoding="utf-8")
        read_ok = await registry.execute("read_allowed_file", {"file_path": str(sample)})
        assert read_ok["content"] == "hello jarvis"

        read_bad = await registry.execute("read_allowed_file", {"file_path": str(tmp_path / "secret.txt")})
        assert read_bad["error"] == "PATH_ACCESS_DENIED"

        # get_system_telemetry and open_app denial
        telem = await registry.execute("get_system_telemetry", {})
        assert "cpu_percent" in telem and "allowed_apps_status" in telem
        denied_open = await registry.execute("open_app", {"app_id": "unknown_app"})
        assert denied_open["error"] == "APP_ACCESS_DENIED"
    finally:
        db.close()


def test_app_controller_lnk_parser_scan_launch_and_toctou_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. Binary .lnk parser + Start Menu scan filtering
    prog_root = tmp_path / "prog_data"
    menu = prog_root / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    menu.mkdir(parents=True)

    app_exe = tmp_path / "MyApp.exe"
    app_exe.write_text("exe", encoding="utf-8")
    rel_to_app = os.path.relpath(app_exe, menu)
    _build_unicode_lnk(menu / "My App.lnk", rel_to_app, "--minimized")

    # Duplicate signature should be deduplicated; cmd.exe shortcut must be blocked
    _build_unicode_lnk(menu / "My App Duplicate.lnk", rel_to_app, "--minimized")
    sys_cmd = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "cmd.exe"
    if sys_cmd.exists():
        _build_unicode_lnk(menu / "Command Prompt.lnk", os.path.relpath(sys_cmd, menu), "")

    target, args = _extract_lnk_via_binary(menu / "My App.lnk")
    assert target == str(app_exe.resolve())
    assert args == ["--minimized"]

    monkeypatch.setenv("ProgramData", str(prog_root))
    monkeypatch.setenv("APPDATA", "")
    # Force binary fallback even if pywin32 were present
    scanned = AppController.scan_start_menu()
    assert len(scanned) == 1
    assert scanned[0]["app_key"] == "my_app"
    assert scanned[0]["process_name"] == "myapp.exe"

    # 2. TOCTOU PID+create_time+name verification and graceful WM_CLOSE
    ctrl = AppController()
    live_procs: dict[int, tuple[float, str]] = {
        5001: (1000.0, "myapp.exe"),
        5002: (9999.0, "myapp.exe"),  # recycled PID (create_time mismatch)
        5003: (1000.0, "cmd.exe"),    # blocked system exe
    }

    class FakeProc:
        def __init__(self, pid: int) -> None:
            if pid not in live_procs:
                raise psutil.NoSuchProcess(pid)
            self._ct, self._name = live_procs[pid]

        def create_time(self) -> float:
            return self._ct

        def name(self) -> str:
            return self._name

    monkeypatch.setattr(psutil, "Process", FakeProc)
    posted_pids: list[set[int]] = []

    def fake_post(pids: set[int], targets_by_pid: dict[int, TargetProcess] | None = None) -> int:
        posted_pids.append(set(pids))
        live_procs.pop(5001, None)  # Simulate 5001 closing gracefully after WM_CLOSE
        return len(pids)

    monkeypatch.setattr(ctrl, "_post_wm_close_to_pids", fake_post)
    targets = [
        TargetProcess(pid=os.getpid(), create_time=1.0, process_name="python.exe"),
        TargetProcess(pid=5001, create_time=1000.0, process_name="myapp.exe"),
        TargetProcess(pid=5002, create_time=1000.0, process_name="myapp.exe"),
        TargetProcess(pid=5003, create_time=1000.0, process_name="cmd.exe"),
    ]
    close_res = ctrl.close_processes_gracefully(targets, wait_seconds=0.3)
    assert posted_pids == [{5001}]
    assert close_res == {"closed": [5001], "pending_save": []}

    # Exercise real 64-bit Win32 EnumWindows ctypes binding cleanly with non-existent PID
    assert AppController._post_wm_close_to_pids({999_999_999}) == 0

    # Simulate unsaved dialog keeping process alive past wait_seconds
    live_procs[5004] = (2000.0, "myapp.exe")
    monkeypatch.setattr(ctrl, "_post_wm_close_to_pids", lambda pids, targets_by_pid=None: len(pids))
    pending_res = ctrl.close_processes_gracefully(
        [TargetProcess(pid=5004, create_time=2000.0, process_name="myapp.exe")],
        wait_seconds=0.05,
    )
    assert pending_res == {"closed": [], "pending_save": [5004]}


def test_app_controller_snapshots_launch_and_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_config(tmp_path / "data")
    db = Database(cfg.db_path)
    try:
        exe = tmp_path / "demo.exe"
        exe.write_text("dummy", encoding="utf-8")
        db.add_app(
            app_key="demo",
            display_name="Demo",
            aliases=["demo"],
            executable_path=str(exe),
            launch_args=["--safe"],
            process_names=["demo.exe"],
        )
        audit = AuditLogger(db)
        guard = AppAllowlistGuard(db, audit)
        app = guard.resolve_app("demo")

        fake_procs = [
            SimpleNamespace(info={"pid": os.getpid(), "name": "demo.exe", "create_time": 1.0}),
            SimpleNamespace(info={"pid": 4, "name": "demo.exe", "create_time": None}),
            SimpleNamespace(info={"pid": 9001, "name": "Demo.EXE", "create_time": 1234.5}),
        ]
        monkeypatch.setattr(psutil, "process_iter", lambda attrs: fake_procs)
        snaps = AppController.running_process_snapshots(app)
        assert snaps == [TargetProcess(pid=9001, create_time=1234.5, process_name="demo.exe")]

        launched_calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_popen(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            launched_calls.append((cmd, kwargs))
            return SimpleNamespace(pid=9999)

        monkeypatch.setattr("jawis.tools.app_controller.subprocess.Popen", fake_popen)
        pid = AppController.launch_app(app, guard)
        assert pid == 9999
        assert launched_calls[0][0] == [str(exe.resolve()), "--safe"]
        assert launched_calls[0][1]["shell"] is False

        telem = SystemTelemetry()
        fast = telem.fast_snapshot()
        assert fast["ram_total_gb"] > 0
        assert fast["jawis_rss_mb"] > 0
        full = telem.snapshot([app])
        assert full["allowed_apps_status"] == [
            {"app_key": "demo", "display_name": "Demo", "running": True}
        ]
    finally:
        db.close()
