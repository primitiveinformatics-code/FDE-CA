from unittest.mock import patch

import keyring.errors
import pytest

from bank_statement_analyzer import secrets_store


class _FakeKeyringBackend:
    """In-memory stand-in for the OS credential vault, keyed the same way
    `keyring` itself is: (service, username) -> password."""

    def __init__(self):
        self._store: dict[tuple[str, str], str] = {}

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def get_password(self, service, username):
        return self._store.get((service, username))

    def delete_password(self, service, username):
        self._store.pop((service, username), None)


@pytest.fixture
def fake_keyring():
    backend = _FakeKeyringBackend()
    with patch("keyring.set_password", side_effect=backend.set_password), \
         patch("keyring.get_password", side_effect=backend.get_password), \
         patch("keyring.delete_password", side_effect=backend.delete_password):
        yield backend


def test_unknown_provider_raises(fake_keyring):
    with pytest.raises(ValueError):
        secrets_store.save_api_key("key", provider="not-a-provider")


def test_providers_are_stored_independently(fake_keyring):
    secrets_store.save_api_key("anthropic-key", provider="anthropic")
    secrets_store.save_api_key("openrouter-key", provider="openrouter")

    assert secrets_store.load_api_key("anthropic") == "anthropic-key"
    assert secrets_store.load_api_key("openrouter") == "openrouter-key"

    secrets_store.delete_api_key("anthropic")
    assert secrets_store.load_api_key("anthropic") is None
    assert secrets_store.load_api_key("openrouter") == "openrouter-key"


def test_has_api_key_reflects_stored_state(fake_keyring):
    assert secrets_store.has_api_key("anthropic") is False
    secrets_store.save_api_key("a-key", provider="anthropic")
    assert secrets_store.has_api_key("anthropic") is True
    assert secrets_store.has_api_key("openrouter") is False


def test_load_api_key_returns_none_when_keyring_unavailable():
    with patch("keyring.get_password", side_effect=keyring.errors.KeyringError):
        assert secrets_store.load_api_key("anthropic") is None


def test_save_api_key_raises_keyring_unavailable_error():
    with patch("keyring.set_password", side_effect=keyring.errors.KeyringError):
        with pytest.raises(secrets_store.KeyringUnavailableError):
            secrets_store.save_api_key("key", provider="anthropic")
