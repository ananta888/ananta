from __future__ import annotations

from agent.services.imap_secret_store_service import (
    FileFallbackImapSecretStore,
    InMemoryImapSecretStore,
    safe_secret_log_text,
)


def test_in_memory_secret_store_roundtrip() -> None:
    store = InMemoryImapSecretStore()
    set_result = store.set_secret(credential_ref="secret://imap/a", secret="app-token-1")
    assert set_result["ok"] is True
    loaded = store.get_secret(credential_ref="secret://imap/a")
    assert loaded["ok"] is True
    assert loaded["secret"] == "app-token-1"
    deleted = store.delete_secret(credential_ref="secret://imap/a")
    assert deleted["ok"] is True


def test_file_fallback_secret_store_marks_insecure_mode(tmp_path) -> None:
    store = FileFallbackImapSecretStore(store_path=tmp_path / "imap-secrets.json")
    set_result = store.set_secret(credential_ref="secret://imap/a", secret="token-2")
    assert set_result["warning_reason_code"] == "insecure_fallback_storage"
    loaded = store.get_secret(credential_ref="secret://imap/a")
    assert loaded["ok"] is True
    assert loaded["warning_reason_code"] == "insecure_fallback_storage"


def test_secret_log_redaction_masks_sensitive_data() -> None:
    text = safe_secret_log_text("password=hunter2 token=abc")
    assert "hunter2" not in text
    assert "[REDACTED_SECRET]" in text
