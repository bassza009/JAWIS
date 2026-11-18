from pathlib import Path

import pytest

from jawis.security.app_guard import AppAccessDeniedError, AppAllowlistGuard
from jawis.security.audit import AuditLogger
from jawis.storage.db import Database


def test_app_guard_resolves_unique_aliases_and_rejects_ambiguity(tmp_path: Path) -> None:
    db = Database(tmp_path / "jawis.db")
    try:
        exe1 = tmp_path / "Code.exe"
        exe2 = tmp_path / "Other.exe"
        exe3 = tmp_path / "Disabled.exe"
        exe1.write_text("dummy", encoding="utf-8")
        exe2.write_text("dummy", encoding="utf-8")
        exe3.write_text("dummy", encoding="utf-8")
        db.add_app(
            app_key="vscode",
            display_name="Visual Studio Code",
            aliases=["code", "โค้ด"],
            executable_path=str(exe1),
            launch_args=[],
            process_names=["Code.exe"],
        )
        db.add_app(
            app_key="other_ide",
            display_name="Other IDE",
            aliases=["code"],
            executable_path=str(exe2),
            launch_args=[],
            process_names=["Other.exe"],
        )
        disabled_id = db.add_app(
            app_key="disabled_app",
            display_name="Disabled App",
            aliases=["disabled"],
            executable_path=str(exe3),
            launch_args=[],
            process_names=["Disabled.exe"],
        )
        db.set_app_enabled(disabled_id, False)

        guard = AppAllowlistGuard(db, AuditLogger(db))
        assert guard.resolve_app("โค้ด").app_key == "vscode"
        assert guard.resolve_app("VISUAL STUDIO CODE").app_key == "vscode"
        assert guard.all_allowed_process_names() == {"code.exe", "other.exe"}

        with pytest.raises(AppAccessDeniedError, match="Ambiguous"):
            guard.resolve_app("code")
        with pytest.raises(AppAccessDeniedError):
            guard.resolve_app("powershell")
        with pytest.raises(AppAccessDeniedError):
            guard.resolve_app("disabled")
        with pytest.raises(AppAccessDeniedError):
            guard.resolve_app("   ")

        blocked = db.list_audits(decision="BLOCKED")
        assert any(entry.event_type == "APP_ACCESS" and entry.target == "powershell" for entry in blocked)
    finally:
        db.close()


def test_app_guard_blocks_scripts_lolbins_and_bad_args(tmp_path: Path) -> None:
    db = Database(tmp_path / "jawis.db")
    try:
        guard = AppAllowlistGuard(db, AuditLogger(db))
        bat = tmp_path / "run.bat"
        bat.write_text("@echo off", encoding="utf-8")
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(bat), ["cmd.exe"])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(tmp_path), ["safe.exe"])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(tmp_path / "missing.exe"), ["missing.exe"])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration("", ["missing.exe"])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(tmp_path / "bad\x00.exe"), ["bad.exe"])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(r"\\192.168.1.10\share\evil.exe", ["evil.exe"])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(r"C:\Windows\System32\cmd.exe", ["cmd.exe"])

        lolbin = tmp_path / "powershell.exe"
        lolbin.write_text("dummy", encoding="utf-8")
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(lolbin), ["powershell.exe"])

        exe = tmp_path / "SafeApp.exe"
        exe.write_text("bin", encoding="utf-8")
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(exe), ["safeapp.exe"], launch_args=["--x & calc.exe"])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(exe), ["safeapp.exe"], launch_args=["--arg\ncalc.exe"])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(exe), ["safeapp.exe"], launch_args=[""])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(exe), ["safeapp.exe"], launch_args=["a" * 261])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(exe), ["safeapp.exe"], launch_args=[f"--a{i}" for i in range(11)])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(exe), [])
        with pytest.raises(AppAccessDeniedError):
            guard.validate_registration(str(exe), ["cmd.exe"])

        resolved = guard.validate_registration(
            str(exe),
            ["SafeApp.exe"],
            launch_args=["--profile", "default"],
        )
        assert resolved == exe.resolve(strict=True)

        blocked = db.list_audits(decision="BLOCKED")
        assert any(entry.event_type == "APP_REGISTER" for entry in blocked)
    finally:
        db.close()
