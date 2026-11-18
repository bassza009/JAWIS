import os
from pathlib import Path
import subprocess

import pytest

from jawis.config import load_config
from jawis.security.audit import AuditLogger
from jawis.security.path_guard import PathAccessDeniedError, PathSecurityGuard
from jawis.storage.db import Database


def _make_guard(tmp_path: Path) -> tuple[PathSecurityGuard, Database, Path]:
    data_dir = tmp_path / "app_data"
    cfg = load_config(data_dir)
    db = Database(cfg.db_path)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    db.add_folder(str(docs_dir), "Docs")
    audit = AuditLogger(db)
    return PathSecurityGuard(cfg, db, audit), db, docs_dir


def _create_junction(link_path: Path, target_path: Path) -> None:
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link_path), str(target_path)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_allows_regular_file_and_normalizes_long_prefix(tmp_path: Path) -> None:
    guard, db, docs_dir = _make_guard(tmp_path)
    try:
        readme = docs_dir / "notes.md"
        readme.write_text("hello", encoding="utf-8")
        resolved = guard.validate_read_path(str(readme))
        assert resolved == readme.resolve()
        assert PathSecurityGuard.normalize_long_path(Path(r"\\?\C:\Docs\a.txt")) == Path(r"C:\Docs\a.txt")
        assert PathSecurityGuard.normalize_long_path(Path(r"\\?\UNC\server\share\a.txt")) == Path(
            r"\\?\UNC\server\share\a.txt"
        )
    finally:
        db.close()


def test_blocks_traversal_secrets_unc_dos_devices_and_relative_paths(tmp_path: Path) -> None:
    guard, db, docs_dir = _make_guard(tmp_path)
    try:
        outside = tmp_path / "secret.txt"
        outside.write_text("nope", encoding="utf-8")

        for name in (".env", ".envrc", "id_ecdsa", "my_api_key.txt"):
            (docs_dir / name).write_text("SECRET", encoding="utf-8")

        ssh_dir = docs_dir / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "id_rsa").write_text("PRIVATE", encoding="utf-8")

        for bad_target in (
            str(docs_dir / ".." / "secret.txt"),
            str(docs_dir / ".env"),
            str(docs_dir / ".envrc"),
            str(docs_dir / "id_ecdsa"),
            str(docs_dir / "my_api_key.txt"),
            str(ssh_dir / "id_rsa"),
            r"\\192.168.1.10\share\doc.txt",
            "//192.168.1.10/share/doc.txt",
            str(docs_dir / "notes.md:hidden_stream"),
            str(docs_dir / "NUL"),
            str(docs_dir / "COM1.txt"),
            str(docs_dir / "NUL "),
            "relative.txt",
            "C:drive_relative.txt",
            "",
            str(docs_dir / "notes.md\x00.txt"),
        ):
            with pytest.raises(PathAccessDeniedError):
                guard.validate_read_path(bad_target)

        audits = db.list_audits(decision="BLOCKED")
        assert len(audits) >= 16
        assert issubclass(PathAccessDeniedError, PermissionError)
    finally:
        db.close()


def test_blocks_drive_root_userprofile_and_system_dirs(tmp_path: Path) -> None:
    guard, db, _ = _make_guard(tmp_path)
    try:
        win_dir = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        local_app = os.environ.get("LOCALAPPDATA")
        jawis_appdata = str(Path(local_app) / "JAWIS") if local_app else ""
        for bad_root in (
            "C:\\",
            os.environ.get("USERPROFILE", ""),
            str(win_dir) if win_dir.exists() else str(guard.config.data_dir),
            str(guard.config.data_dir),
            jawis_appdata,
            os.environ.get("SystemDrive", "C:") + "\\Users",
        ):
            if bad_root:
                with pytest.raises(PathAccessDeniedError):
                    guard.validate_folder_root(bad_root)

        valid_root = tmp_path / "projects"
        valid_root.mkdir()
        assert guard.validate_folder_root(str(valid_root)) == valid_root.resolve()

        not_a_dir = valid_root / "file.txt"
        not_a_dir.write_text("content", encoding="utf-8")
        with pytest.raises(PathAccessDeniedError):
            guard.validate_folder_root(str(not_a_dir))
    finally:
        db.close()


def test_blocks_ntfs_junctions_and_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    guard, db, docs_dir = _make_guard(tmp_path)
    try:
        outside_dir = tmp_path / "outside"
        outside_dir.mkdir()
        (outside_dir / "leak.txt").write_text("leak", encoding="utf-8")

        inside_sub = docs_dir / "sub"
        inside_sub.mkdir()
        (inside_sub / "ok.txt").write_text("ok", encoding="utf-8")

        junc_out = docs_dir / "junc_out"
        junc_in = docs_dir / "junc_in"
        _create_junction(junc_out, outside_dir)
        _create_junction(junc_in, inside_sub)

        assert PathSecurityGuard.is_reparse_point(junc_out) is True
        assert PathSecurityGuard.is_reparse_point(inside_sub) is False

        for bad_path in (
            str(junc_out),
            str(junc_out / "leak.txt"),
            str(junc_in),
            str(junc_in / "ok.txt"),
        ):
            with pytest.raises(PathAccessDeniedError):
                guard.validate_read_path(bad_path)

        with pytest.raises(PathAccessDeniedError):
            guard.validate_folder_root(str(junc_out))

        # Swapping a previously allowlisted root on disk with a junction must not grant access.
        swapped_root = tmp_path / "swapped_root"
        swapped_root.mkdir()
        db.add_folder(str(swapped_root), "Swapped")
        swapped_root.rmdir()
        _create_junction(swapped_root, outside_dir)
        with pytest.raises(PathAccessDeniedError):
            guard.validate_read_path(str(swapped_root / "leak.txt"))

        # Verify symlink detection branch deterministically even without SeCreateSymbolicLinkPrivilege.
        plain_file = docs_dir / "plain.txt"
        plain_file.write_text("data", encoding="utf-8")
        orig_is_symlink = Path.is_symlink
        monkeypatch.setattr(
            Path,
            "is_symlink",
            lambda self: True if self == plain_file else orig_is_symlink(self),
        )
        assert PathSecurityGuard.is_reparse_point(plain_file) is True
        with pytest.raises(PathAccessDeniedError):
            guard.validate_read_path(str(plain_file))
    finally:
        db.close()


def test_blocks_disabled_folders_outside_paths_and_records_audit_details(tmp_path: Path) -> None:
    guard, db, docs_dir = _make_guard(tmp_path)
    try:
        note = docs_dir / "note.txt"
        note.write_text("safe", encoding="utf-8")

        outside_dir = tmp_path / "other"
        outside_dir.mkdir()
        outside_file = outside_dir / "other.txt"
        outside_file.write_text("outside", encoding="utf-8")

        with pytest.raises(PathAccessDeniedError):
            guard.validate_read_path(str(outside_file), tool_name="search_allowed_files")

        with pytest.raises(PathAccessDeniedError):
            guard.validate_read_path(str(docs_dir / "missing.txt"))

        assert guard.is_sensitive_path(docs_dir / "My_Token_File.txt") is True
        assert guard.is_sensitive_path(docs_dir / "Server.PEM") is True
        assert guard.is_sensitive_path(docs_dir / ".GIT" / "config") is True
        assert guard.is_sensitive_path(docs_dir / "normal.txt") is False

        folders = db.list_folders()
        assert len(folders) == 1
        db.set_folder_enabled(folders[0].id, False)
        with pytest.raises(PathAccessDeniedError):
            guard.validate_read_path(str(note))

        blocked = db.list_audits(decision="BLOCKED")
        assert any(entry.tool_name == "search_allowed_files" and entry.actor == "guard" for entry in blocked)

        audit = AuditLogger(db)
        row_id = audit.record("PATH_ACCESS", "user", "add_folder", str(docs_dir), "ALLOWED")
        assert row_id > 0
        allowed = db.list_audits(decision="ALLOWED")
        assert len(allowed) == 1
        assert allowed[0].target == str(docs_dir)
        assert allowed[0].detail == ""
    finally:
        db.close()
