from __future__ import annotations

from agent.providers.redaction import redact_provider_payload


def test_redaction_masks_nested_secret_like_keys() -> None:
    payload = {
        "token": "abc",
        "nested": {
            "api_key": "secret",
            "items": [{"password": "pw"}, {"safe": "ok"}],
        },
    }
    redacted = redact_provider_payload(payload)
    assert redacted["token"] == "***REDACTED***"
    assert redacted["nested"]["api_key"] == "***REDACTED***"
    assert redacted["nested"]["items"][0]["password"] == "***REDACTED***"
    assert redacted["nested"]["items"][1]["safe"] == "ok"


def test_redaction_masks_configured_secret_refs_in_values() -> None:
    payload = {"secret_ref": "vault://my-token", "metadata": {"trace": "ok"}}
    redacted = redact_provider_payload(payload, secret_refs=["vault://my-token"])
    assert redacted["secret_ref"] == "***REDACTED***"
    assert redacted["metadata"]["trace"] == "ok"


def test_redaction_keeps_safe_payload_unchanged() -> None:
    payload = {"status": "ok", "count": 2, "tags": ["a", "b"]}
    assert redact_provider_payload(payload) == payload
