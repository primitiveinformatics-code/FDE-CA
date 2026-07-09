"""API key storage via the OS credential vault (P0: never plaintext).

Windows -> Credential Manager, macOS -> Keychain, Linux -> Secret
Service (gnome-keyring/kwallet), all through the cross-platform
`keyring` package. Each LLM provider (see `config.LLM_PROVIDERS`) gets
its own keyring entry, so switching the active provider in the UI never
overwrites the other provider's stored key.
"""
from __future__ import annotations

import keyring
import keyring.errors

from bank_statement_analyzer.config import DEFAULT_CONFIG, DEFAULT_LLM_PROVIDER


class KeyringUnavailableError(RuntimeError):
    """Raised when the OS has no usable credential-vault backend."""


def _username_for(provider: str) -> str:
    try:
        return DEFAULT_CONFIG.keyring_usernames[provider]
    except KeyError:
        raise ValueError(f"Unknown LLM provider: {provider!r}") from None


def save_api_key(api_key: str, provider: str = DEFAULT_LLM_PROVIDER) -> None:
    try:
        keyring.set_password(DEFAULT_CONFIG.keyring_service, _username_for(provider), api_key)
    except keyring.errors.KeyringError as e:
        raise KeyringUnavailableError(
            "No OS credential vault backend is available on this machine, so the API key "
            "can't be stored securely (e.g. install gnome-keyring/kwallet on Linux, or use "
            "Windows Credential Manager / macOS Keychain, which are built in)."
        ) from e


def load_api_key(provider: str = DEFAULT_LLM_PROVIDER) -> str | None:
    try:
        return keyring.get_password(DEFAULT_CONFIG.keyring_service, _username_for(provider))
    except keyring.errors.KeyringError:
        return None


def has_api_key(provider: str = DEFAULT_LLM_PROVIDER) -> bool:
    """Local-only check for whether a key is already stored — used by the
    UI's status indicator so the user doesn't have to re-open the key
    dialog just to find out. Never makes a network call."""
    return bool(load_api_key(provider))


def delete_api_key(provider: str = DEFAULT_LLM_PROVIDER) -> None:
    try:
        keyring.delete_password(DEFAULT_CONFIG.keyring_service, _username_for(provider))
    except keyring.errors.KeyringError:
        pass
