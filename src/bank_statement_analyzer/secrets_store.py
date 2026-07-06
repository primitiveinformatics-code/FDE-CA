"""API key storage via the OS credential vault (P0: never plaintext).

Windows -> Credential Manager, macOS -> Keychain, Linux -> Secret
Service (gnome-keyring/kwallet), all through the cross-platform
`keyring` package.
"""
from __future__ import annotations

import keyring
import keyring.errors

from bank_statement_analyzer.config import DEFAULT_CONFIG


class KeyringUnavailableError(RuntimeError):
    """Raised when the OS has no usable credential-vault backend."""


def save_api_key(api_key: str) -> None:
    try:
        keyring.set_password(DEFAULT_CONFIG.keyring_service, DEFAULT_CONFIG.keyring_username, api_key)
    except keyring.errors.KeyringError as e:
        raise KeyringUnavailableError(
            "No OS credential vault backend is available on this machine, so the API key "
            "can't be stored securely (e.g. install gnome-keyring/kwallet on Linux, or use "
            "Windows Credential Manager / macOS Keychain, which are built in)."
        ) from e


def load_api_key() -> str | None:
    try:
        return keyring.get_password(DEFAULT_CONFIG.keyring_service, DEFAULT_CONFIG.keyring_username)
    except keyring.errors.KeyringError:
        return None


def delete_api_key() -> None:
    try:
        keyring.delete_password(DEFAULT_CONFIG.keyring_service, DEFAULT_CONFIG.keyring_username)
    except keyring.errors.KeyringError:
        pass
