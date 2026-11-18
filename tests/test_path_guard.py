import ctypes
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

        sub_dir = docs_dir / "sub"
        sub_dir.mkdir()
        notes = docs_dir / "notes.md"
        notes.write_text("ok", encoding="utf-8")

        for bad_target in (
            str(docs_dir / ".." / "secret.txt"),
            str(sub_dir / ".." / "notes.md"),
            f"{docs_dir.as_posix()}/sub/../notes.md",
            str(docs_dir / ".env"),
            str(docs_dir / ".envrc"),
            str(docs_dir / "id_ecdsa"),
            str(docs_dir / "my_api_key.txt"),
            str(ssh_dir / "id_rsa"),
            r"\\192.168.1.10\share\doc.txt",
            "//192.168.1.10/share/doc.txt",
            r"\/server\share\x.txt",
            r"/\127.0.0.1\c$\Windows",
            r"\/.\NUL",
            r"\\.\NUL",
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
        assert len(audits) >= 22
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
            str(Path.home() / ".jawis"),
            os.environ.get("SystemDrive", "C:") + "\\Users",
            r"\/server\share",
            r"/\127.0.0.1\c$\Windows",
        ):
            if bad_root:
                with pytest.raises(PathAccessDeniedError):
                    guard.validate_folder_root(bad_root)

        assert guard.is_sensitive_path(Path(r"\\server\share\doc.txt")) is True

        valid_root = tmp_path / "projects"
        valid_root.mkdir()
        assert guard.validate_folder_root(str(valid_root)) == valid_root.resolve()

        nested_sub = valid_root / "sub"
        nested_sub.mkdir()
        with pytest.raises(PathAccessDeniedError):
            guard.validate_folder_root(str(nested_sub / ".."))

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
            str(junc_in / ".." / "sub" / "ok.txt"),
        ):
            with pytest.raises(PathAccessDeniedError):
                guard.validate_read_path(bad_path)

        (outside_dir / "nested").mkdir()
        with pytest.raises(PathAccessDeniedError):
            guard.validate_folder_root(str(junc_out))
        with pytest.raises(PathAccessDeniedError):
            guard.validate_folder_root(str(junc_out / "nested"))

        # Swapping an ancestor directory above a previously allowlisted root must also block access.
        parent_dir = tmp_path / "parent_dir"
        parent_dir.mkdir()
        nested_root = parent_dir / "allowed"
        nested_root.mkdir()
        (nested_root / "ok.txt").write_text("ok", encoding="utf-8")
        db.add_folder(str(nested_root), "NestedAllowed")

        evil_parent = tmp_path / "evil_parent"
        evil_parent.mkdir()
        evil_allowed = evil_parent / "allowed"
        evil_allowed.mkdir()
        (evil_allowed / "ok.txt").write_text("evil", encoding="utf-8")

        (nested_root / "ok.txt").unlink()
        nested_root.rmdir()
        parent_dir.rmdir()
        _create_junction(parent_dir, evil_parent)

        with pytest.raises(PathAccessDeniedError):
            guard.validate_read_path(str(parent_dir / "allowed" / "ok.txt"))
        with pytest.raises(PathAccessDeniedError):
            guard.validate_read_path(str(evil_allowed / "ok.txt"))

        # 8.3 short paths that lexically differ from stored root_path must still fail closed on junctions.
        long_root = tmp_path / "Long Folder Name"
        long_root.mkdir()
        db.add_folder(str(long_root), "LongFolder")
        long_sub = long_root / "target_sub"
        long_sub.mkdir()
        (long_sub / "allowed_ok.txt").write_text("ok", encoding="utf-8")
        long_junc = long_root / "junc_link"
        _create_junction(long_junc, long_sub)

        junc_file = long_junc / "allowed_ok.txt"
        short_buf = ctypes.create_unicode_buffer(1024)
        short_len = ctypes.windll.kernel32.GetShortPathNameW(str(junc_file), short_buf, len(short_buf))
        assert short_len > 0
        short_junc_file = short_buf.value
        assert short_junc_file != str(junc_file)
        with pytest.raises(PathAccessDeniedError):
            guard.validate_read_path(short_junc_file)
        junc_audit = db.list_audits(decision="BLOCKED", limit=1)
        assert "junction" in junc_audit[0].detail.lower()

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


def test_rejects_parent_traversal_mixed_unc_and_empty_system_drive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Test name intentionally avoids hard-denied substrings like "secret" so only path rules trigger.
    guard, db, docs_dir = _make_guard(tmp_path)
    try:
        sub_dir = docs_dir / "sub"
        sub_dir.mkdir()
        notes = docs_dir / "notes.md"
        notes.write_text("ok", encoding="utf-8")

        for traversal in (
            str(sub_dir / ".." / "notes.md"),
            f"{docs_dir.as_posix()}/sub/../notes.md",
        ):
            with pytest.raises(PathAccessDeniedError):
                guard.validate_read_path(traversal)
        latest = db.list_audits(decision="BLOCKED", limit=2)
        assert all(".." in entry.detail for entry in latest)

        for mixed_unc in (
            r"\/server\share\x.txt",
            r"/\127.0.0.1\c$\Windows",
            r"\/.\NUL",
        ):
            with pytest.raises(PathAccessDeniedError):
                guard.validate_read_path(mixed_unc)
        unc_audits = db.list_audits(decision="BLOCKED", limit=3)
        assert all("UNC, ADS" in entry.detail for entry in unc_audits)

        monkeypatch.setenv("SystemDrive", "")
        guard_empty_sd = PathSecurityGuard(guard.config, db, AuditLogger(db))
        with pytest.raises(PathAccessDeniedError):
            guard_empty_sd.validate_folder_root(r"C:\Users")
    finally:
        db.close()
