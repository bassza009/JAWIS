from pathlib import Path
from keyring.errors import KeyringError, PasswordDeleteError
import pytest

from jawis.config import load_config
from jawis.storage.db import Database
from jawis.storage.secrets import SecretVault


class MemoryKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, key: str) -> str | None:
        return self.store.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self.store[(service, key)] = value

    def delete_password(self, service: str, key: str) -> None:
        self.store.pop((service, key), None)


class LockedKeyring(MemoryKeyring):
    def get_password(self, service: str, key: str) -> str | None:
        raise KeyringError("Credential store locked")

    def delete_password(self, service: str, key: str) -> None:
        raise PasswordDeleteError("Credential missing or locked")


def test_database_crud_pruning_and_safe_close(tmp_path: Path) -> None:
    db = Database(tmp_path / "jawis.db")
    try:
        app_id = db.add_app(
            app_key="notepad",
            display_name="Notepad",
            aliases=["โน้ตแพด", "notepad"],
            executable_path=r"C:\Windows\System32\notepad.exe",
            launch_args=[],
            process_names=["notepad.exe"],
        )
        assert len(db.list_apps()) == 1
        db.set_app_enabled(app_id, False)
        assert db.list_apps(only_enabled=True) == []

        folder_id = db.add_folder(str(tmp_path), "Workspace")
        db.set_folder_enabled(folder_id, False)
        assert db.list_folders(only_enabled=True) == []
        db.delete_folder(folder_id)

        for i in range(5):
            db.append_audit("TOOL", "ai", "open_app", f"app{i}", "ALLOWED", "ok")
            db.append_transcript("user", f"msg{i}")
        db.prune_old_records(max_audits=2, max_transcripts=3)
        assert len(db.list_audits(limit=20)) == 2
        assert len(db.list_transcripts(limit=20)) == 3

        db.set_setting("ai_provider", "gemini")
        assert db.get_setting("ai_provider") == "gemini"
    finally:
        db.close()
    assert db.is_closed is True
    assert db.list_apps() == []  # Safe no-op after close


def test_database_additional_queries_and_post_close_guards(tmp_path: Path) -> None:
    cfg = load_config(data_dir=tmp_path / "appdata")
    db = Database(cfg.db_path)
    try:
        app_id = db.add_app(
            app_key="  Code ",
            display_name=" VS Code ",
            aliases=["vscode"],
            executable_path=r"C:\Program Files\VSCode\Code.exe",
            launch_args=["--new-window"],
            process_names=["Code.exe"],
        )
        apps = db.list_apps()
        assert len(apps) == 1
        assert apps[0].app_key == "code"
        assert apps[0].display_name == "VS Code"
        assert apps[0].process_names == ["code.exe"]

        db.delete_app(app_id)
        assert db.list_apps() == []

        db.append_audit("TOOL", "ai", "open_app", "notepad", "ALLOWED", "ok")
        db.append_audit("TOOL", "ai", "read_file", ".env", "DENIED", "secret")
        denied = db.list_audits(decision="DENIED")
        assert len(denied) == 1
        assert denied[0].target == ".env"

        db.append_transcript("user", "first")
        db.append_transcript("assistant", "second")
        db.append_transcript("user", "third")
        latest = db.list_transcripts(limit=2)
        assert [t.content for t in latest] == ["second", "third"]

        db.clear_transcripts()
        assert db.list_transcripts() == []

        db.set_setting("ai_provider", "gemini")
        db.set_setting("ai_provider", "openai")
        assert db.get_setting("ai_provider") == "openai"
        assert db.get_setting("missing_key", "fallback") == "fallback"
    finally:
        db.close()

    # Double close and post-close operations must be safe no-ops
    db.close()
    assert db.is_closed is True
    assert db.add_app(
        app_key="x",
        display_name="X",
        aliases=[],
        executable_path="x.exe",
        launch_args=[],
        process_names=[],
    ) == 0
    assert db.add_folder(str(tmp_path), "X") == 0
    assert db.append_audit("T", "a", "t", "x", "ALLOWED") == 0
    assert db.append_transcript("user", "hello") == 0
    assert db.list_folders() == []
    assert db.list_audits() == []
    assert db.list_transcripts() == []
    assert db.get_setting("ai_provider", "default") == "default"
    db.set_app_enabled(1, True)
    db.delete_app(1)
    db.set_folder_enabled(1, True)
    db.delete_folder(1)
    db.clear_transcripts()
    db.prune_old_records()
    db.set_setting("k", "v")


def test_secret_vault_handles_keyring_errors() -> None:
    vault = SecretVault(service_name="JAWIS_TEST", backend=MemoryKeyring())
    vault.set_api_key("gemini", "secret-key-123")
    assert vault.has_api_key("gemini") is True
    vault.delete_api_key("gemini")
    assert vault.get_api_key("gemini") is None

    locked = SecretVault(service_name="JAWIS_TEST", backend=LockedKeyring())
    assert locked.get_api_key("gemini") is None
    assert locked.has_api_key("gemini") is False
    locked.delete_api_key("gemini")  # Must not raise


def test_secret_vault_validation_and_provider_normalization(tmp_path: Path) -> None:
    cfg = load_config(data_dir=tmp_path / "appdata")
    vault = SecretVault(service_name=cfg.keyring_service, backend=MemoryKeyring())
    vault.set_api_key(" OpenAI ", "  sk-test-123  ")
    assert vault.get_api_key("openai") == "sk-test-123"
    assert vault.has_api_key("OPENAI") is True

    with pytest.raises(ValueError, match="Unsupported provider"):
        vault.set_api_key("anthropic", "key")

    with pytest.raises(ValueError, match="API key must not be empty"):
        vault.set_api_key("gemini", "   ")
