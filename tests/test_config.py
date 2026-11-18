from pathlib import Path
import tomllib

from jawis.config import (
    BLOCKED_SYSTEM_EXECUTABLES,
    HARD_DENIED_DIR_NAMES,
    HARD_DENIED_FILENAME_PATTERNS,
    load_config,
)


def test_load_config_uses_explicit_data_dir(tmp_path: Path) -> None:
    target = tmp_path / "nested_data"
    cfg = load_config(data_dir=target)
    assert cfg.data_dir == target.resolve()
    assert cfg.db_path == target.resolve() / "jawis.db"
    assert cfg.webview_dir == target.resolve() / "webview"
    assert cfg.confirm_timeout_seconds == 10.0
    assert cfg.max_audio_ring_seconds == 2.5
    assert cfg.max_file_text_chars == 24_000
    assert cfg.max_text_file_bytes == 65_536
    assert cfg.max_document_file_bytes == 15_728_640
    assert cfg.max_decompressed_zip_bytes == 26_214_400
    assert cfg.max_backend_idle_rss_mb == 85
    assert cfg.max_host_idle_rss_mb == 140
    assert cfg.max_wakeword_rss_mb == 205


def test_pyproject_has_websockets_and_forbids_heavy_ml() -> None:
    raw = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    deps = " ".join(raw["project"]["dependencies"]).lower()
    assert "websockets" in deps
    for banned in ("torch", "tensorflow", "whisper", "pydub", "transformers"):
        assert banned not in deps


def test_security_constants_cover_secrets_dirs_and_lolbins() -> None:
    assert ".env" in HARD_DENIED_FILENAME_PATTERNS
    assert ".envrc" in HARD_DENIED_FILENAME_PATTERNS
    assert "*.kdbx" in HARD_DENIED_FILENAME_PATTERNS
    assert ".git" in HARD_DENIED_DIR_NAMES
    assert ".docker" in HARD_DENIED_DIR_NAMES
    for exe in ("powershell.exe", "cmd.exe", "mshta.exe", "wscript.exe", "rundll32.exe", "wt.exe"):
        assert exe in BLOCKED_SYSTEM_EXECUTABLES
