from __future__ import annotations

import base64
import copy
import json
from pathlib import Path

import pytest

from ananta_contracts.webrtc_security import (
    SecureEnvelopeError,
    open_secure_envelope,
    validate_secure_envelope,
)

FIXTURE = Path("tests/fixtures/webrtc/crypto_vectors/secure_envelope_vectors.v1.json")


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_golden_envelope_decrypts_and_payload_type_is_authenticated() -> None:
    fixture = _fixture()
    envelope = validate_secure_envelope(fixture["envelope"], now_ms=fixture["now_ms"])
    assert open_secure_envelope(key=base64.b64decode(fixture["key_b64"]), envelope=envelope) == base64.b64decode(
        fixture["plaintext_b64"]
    )
    swapped = copy.deepcopy(fixture["envelope"])
    swapped["payload_type"] = "pair.control"
    with pytest.raises(SecureEnvelopeError, match="authentication_failed"):
        open_secure_envelope(
            key=base64.b64decode(fixture["key_b64"]),
            envelope=validate_secure_envelope(swapped, now_ms=fixture["now_ms"]),
        )


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda value: value.update({"unknown": True}), "unknown_field"),
        (lambda value: value.update({"sequence": float("nan")}), "sequence_invalid"),
        (lambda value: value.update({"nonce_b64": "AA=="}), "nonce_invalid"),
        (lambda value: value.update({"sender_id": ""}), "sender_invalid"),
    ],
)
def test_closed_and_bounded_contract_rejects_invalid_values(mutate, reason: str) -> None:
    fixture = _fixture()
    raw = copy.deepcopy(fixture["envelope"])
    mutate(raw)
    with pytest.raises(SecureEnvelopeError) as exc:
        validate_secure_envelope(raw, now_ms=fixture["now_ms"])
    assert exc.value.reason_code == reason
