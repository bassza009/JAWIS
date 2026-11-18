"""Windows Credential Manager wrapper for provider API keys."""

from __future__ import annotations

from typing import Protocol
import keyring
from keyring.errors import KeyringError, PasswordDeleteError


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...
    def set_password(self, service_name: str, username: str, password: str) -> None: ...
    def delete_password(self, service_name: str, username: str) -> None: ...


class SecretVault:
    """Stores provider API keys outside project files and SQLite."""

    def __init__(
        self,
        service_name: str = "JAWIS_ASSISTANT",
        backend: KeyringBackend | None = None,
    ) -> None:
        self.service_name = service_name
        self._backend: KeyringBackend = backend if backend is not None else keyring

    @staticmethod
    def _key_name(provider: str) -> str:
        clean = provider.strip().lower()
        if clean not in {"gemini", "openai"}:
            raise ValueError(f"Unsupported provider: {provider}")
        return f"{clean}_api_key"

    def set_api_key(self, provider: str, api_key: str) -> None:
        clean_key = api_key.strip()
        if not clean_key:
            raise ValueError("API key must not be empty")
        self._backend.set_password(self.service_name, self._key_name(provider), clean_key)

    def get_api_key(self, provider: str) -> str | None:
        try:
            return self._backend.get_password(self.service_name, self._key_name(provider))
        except KeyringError:
            return None

    def delete_api_key(self, provider: str) -> None:
        try:
            self._backend.delete_password(self.service_name, self._key_name(provider))
        except (PasswordDeleteError, KeyringError):
            return

    def has_api_key(self, provider: str) -> bool:
        value = self.get_api_key(provider)
        return bool(value and value.strip())
