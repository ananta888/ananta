from __future__ import annotations

import time

from agent.services.workflow_auth import sign_callback, verify_callback_signature


def test_valid_signature() -> None:
    payload = sign_callback(secret="s", correlation_id="c", provider="p")
    assert verify_callback_signature(secret="s", correlation_id="c", provider="p", timestamp=payload["timestamp"], signature=payload["signature"])


def test_invalid_signature() -> None:
    payload = sign_callback(secret="s", correlation_id="c", provider="p")
    assert not verify_callback_signature(secret="s", correlation_id="c", provider="p", timestamp=payload["timestamp"], signature="bad")


def test_expired_signature() -> None:
    ts = int(time.time()) - 10000
    payload = sign_callback(secret="s", correlation_id="c", provider="p", timestamp=ts)
    assert not verify_callback_signature(secret="s", correlation_id="c", provider="p", timestamp=payload["timestamp"], signature=payload["signature"], max_age_seconds=30)


def test_wrong_correlation_id_rejected() -> None:
    payload = sign_callback(secret="s", correlation_id="c1", provider="p")
    assert not verify_callback_signature(secret="s", correlation_id="c2", provider="p", timestamp=payload["timestamp"], signature=payload["signature"])
