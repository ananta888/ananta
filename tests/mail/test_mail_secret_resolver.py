from __future__ import annotations

import pytest

from agent.services.mail_secret_resolver import (
    EnvFileMailSecretResolver,
    MailSecretResolutionError,
    StoreMailSecretResolver,
    redact_mail_secret_text,
)


class _Store:
    def __init__(self, result):
        self.result = result

    def get_secret(self, *, credential_ref: str):
        del credential_ref
        return self.result


def test_store_resolver_rejects_insecure_legacy_plaintext_by_default() -> None:
    resolver = StoreMailSecretResolver(
        store=_Store(
            {
                "ok": True,
                "reason_code": "ok",
                "secret": "raw-secret",
                "warning_reason_code": "insecure_fallback_storage",
            }
        )
    )
    with pytest.raises(MailSecretResolutionError, match="mail_insecure_secret_store_forbidden"):
        resolver.resolve("secret://mail/a")


def test_file_secret_resolver_rejects_outside_allowed_root_and_redacts() -> None:
    resolver = EnvFileMailSecretResolver(allowed_file_roots=("/run/secrets",))
    with pytest.raises(MailSecretResolutionError, match="mail_secret_path_not_allowed"):
        resolver.resolve("file:///etc/hostname")
    redacted = redact_mail_secret_text("Authorization: Bearer abc", known_secrets=("abc",))
    assert "abc" not in redacted
