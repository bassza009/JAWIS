"""Application configuration, empirical memory budgets, and immutable security constants."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

HARD_DENIED_FILENAME_PATTERNS: tuple[str, ...] = (
    ".env",
    ".env.*",
    ".env_*",
    ".envrc",
    "*.pem",
    "*.key",
    "*.pfx",
    "*.p12",
    "*.ppk",
    "*.kdbx",
    "*.ovpn",
    "*.jks",
    "*.keystore",
    "*.gpg",
    "*.asc",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "id_dsa*",
    "credentials*.json",
    ".git-credentials",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "_netrc",
    "*secret*",
    "*token*",
    "*password*",
    "*passwd*",
    "*credential*",
    "*api_key*",
    "*apikey*",
)

HARD_DENIED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".docker",
        ".kube",
        ".git",
        ".terraform",
        "__pycache__",
        "$recycle.bin",
        "system volume information",
    }
)

BLOCKED_SYSTEM_EXECUTABLES: frozenset[str] = frozenset(
    {
        "cmd.exe",
        "powershell.exe",
        "powershell_ise.exe",
        "pwsh.exe",
        "wscript.exe",
        "cscript.exe",
        "mshta.exe",
        "rundll32.exe",
        "regsvr32.exe",
        "msiexec.exe",
        "certutil.exe",
        "bitsadmin.exe",
        "schtasks.exe",
        "sc.exe",
        "wmic.exe",
        "taskkill.exe",
        "wt.exe",
        "bash.exe",
        "wsl.exe",
        "regedit.exe",
        "reg.exe",
        "taskmgr.exe",
        "shutdown.exe",
        "format.com",
        "diskpart.exe",
        "bcdedit.exe",
        "control.exe",
        "mmc.exe",
        "explorer.exe",
        "csrss.exe",
        "wininit.exe",
        "services.exe",
        "lsass.exe",
        "svchost.exe",
    }
)


@dataclass(frozen=True)
class AppConfig:
    """Immutable runtime and memory-budget configuration for J.A.R.V.I.S."""

    data_dir: Path
    db_path: Path
    models_dir: Path
    webview_dir: Path
    confirm_timeout_seconds: float = 10.0
    max_audio_ring_seconds: float = 2.5
    max_utterance_seconds: float = 15.0
    max_file_text_chars: int = 24_000
    max_text_file_bytes: int = 65_536
    max_document_file_bytes: int = 15_728_640
    max_decompressed_zip_bytes: int = 26_214_400
    max_pdf_pages_per_read: int = 10
    max_search_results: int = 20
    max_search_depth: int = 6
    max_search_entries: int = 5_000
    max_search_seconds: float = 2.0
    max_backend_idle_rss_mb: int = 85
    max_host_idle_rss_mb: int = 140
    max_wakeword_rss_mb: int = 205
    sample_rate_hz: int = 16_000
    frame_duration_ms: int = 30
    keyring_service: str = "JAWIS_ASSISTANT"


def default_data_dir() -> Path:
    """Return the per-user local data directory outside project workspaces."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "JAWIS"
    return Path.home() / ".jawis"


def load_config(data_dir: Path | None = None) -> AppConfig:
    """Create local data directories first, then resolve canonical paths cleanly."""
    raw_dir = data_dir or default_data_dir()
    raw_dir.mkdir(parents=True, exist_ok=True)
    resolved_dir = raw_dir.resolve(strict=True)
    models_dir = resolved_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    webview_dir = resolved_dir / "webview"
    webview_dir.mkdir(parents=True, exist_ok=True)
    return AppConfig(
        data_dir=resolved_dir,
        db_path=resolved_dir / "jawis.db",
        models_dir=models_dir,
        webview_dir=webview_dir,
    )
