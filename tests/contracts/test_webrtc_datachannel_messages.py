from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ananta_contracts.webrtc_datachannel import (
    CHUNK_VERSION,
    CONTRACT_VERSION,
    TRAFFIC_CLASS_LIMITS,
    DataChannelContractError,
    bound_chunk_id,
    encode_wire_message,
    parse_message,
    parse_wire_message,
    validate_chunk,
    validate_message,
)

ROOT = Path(__file__).resolve().parents[2]


def _message(payload: bytes = b"", traffic_class: str = "control") -> dict:
    return {
        "version": CONTRACT_VERSION,
        "traffic_class": traffic_class,
        "message_id": "message-1",
        "session_id": "session-1",
        "epoch": 1,
        "sender_id": "sender-1",
        "audience_id": "receiver-1",
        "sequence": 1,
        "expires_at_ms": 10_000,
        "compression": "none",
        "security": {"algorithm": "AES-GCM-256", "key_id": "key-1"},
        "payload_bytes": len(payload),
        "payload_digest": hashlib.sha256(payload).hexdigest(),
        "ciphertext": base64.b64encode(payload).decode(),
    }


def _chunk(payload: bytes = b"x") -> dict:
    digest = hashlib.sha256(payload).hexdigest()
    return {
        "version": CHUNK_VERSION,
        "chunk_id": bound_chunk_id(session_id="session-1", epoch=1, sender_id="sender-1", payload_digest=digest),
        "message_id": "message-1",
        "session_id": "session-1",
        "epoch": 1,
        "sender_id": "sender-1",
        "traffic_class": "control",
        "index": 0,
        "total": 1,
        "chunk_bytes": len(payload),
        "total_bytes": len(payload),
        "expires_at_ms": 10_000,
        "payload_digest": digest,
        "data": base64.b64encode(payload).decode(),
    }


@pytest.mark.parametrize("traffic_class", sorted(TRAFFIC_CLASS_LIMITS))
def test_each_traffic_class_accepts_empty_and_exact_maximum(traffic_class: str) -> None:
    assert validate_message(_message(traffic_class=traffic_class)).ciphertext == b""
    maximum = TRAFFIC_CLASS_LIMITS[traffic_class]
    assert len(validate_message(_message(b"x" * maximum, traffic_class)).ciphertext) == maximum
    with pytest.raises(DataChannelContractError, match="payload_too_large"):
        validate_message(_message(b"x" * (maximum + 1), traffic_class))


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("version", "v0", "unsupported_version"),
        ("traffic_class", "generic", "unknown_traffic_class"),
        ("compression", "gzip", "unsupported_compression"),
        ("security", {"algorithm": "none", "key_id": "k"}, "unsupported_security_algorithm"),
    ],
)
def test_closed_contract_never_uses_fallback_parser(field: str, value, reason: str) -> None:
    candidate = _message(b"ciphertext")
    candidate[field] = value
    with pytest.raises(DataChannelContractError, match=reason):
        validate_message(candidate)
    candidate = _message()
    candidate["unknown_security_hint"] = True
    with pytest.raises(DataChannelContractError, match="unknown_field"):
        validate_message(candidate)


def test_wire_size_is_checked_before_json_parse() -> None:
    with pytest.raises(DataChannelContractError, match="wire_message_too_large"):
        parse_message(b"[" + b"0" * 1_500_000)


def test_framed_wire_header_is_bounded_and_checked_before_json_parse() -> None:
    candidate = _message(b"golden", "transcript")
    framed = encode_wire_message(candidate)
    assert parse_wire_message(framed).ciphertext == b"golden"
    with pytest.raises(DataChannelContractError, match="payload_too_large"):
        parse_wire_message(b"ANANTA-DC1 evidence_bulk 1048577 1\n{")
    with pytest.raises(DataChannelContractError, match="wire_body_size_mismatch"):
        parse_wire_message(b"ANANTA-DC1 control 0 100\n{}")


def test_python_uses_the_shared_browser_golden_cases() -> None:
    cases = json.loads((ROOT / "tests/fixtures/webrtc/datachannel_message_cases.json").read_text())
    for case in cases:
        candidate = _message(bytes.fromhex(case["payload_hex"]))
        candidate["traffic_class"] = case["traffic_class"]
        if case["expected"] == "accept":
            parsed = parse_wire_message(encode_wire_message(candidate))
            assert parsed.traffic_class == case["traffic_class"], case["name"]
        else:
            with pytest.raises(DataChannelContractError) as error:
                encode_wire_message(candidate)
            assert error.value.reason_code == case["expected"], case["name"]


def test_chunk_bounds_context_and_cross_field_invariants() -> None:
    assert validate_chunk(_chunk()).index == 0
    for field, value, reason in (
        ("total", 10**9, "invalid_integer"),
        ("total", -1, "invalid_integer"),
        ("total", float("inf"), "invalid_integer"),
        ("index", 1, "chunk_index_out_of_range"),
        ("chunk_id", "0" * 64, "chunk_context_mismatch"),
    ):
        candidate = _chunk()
        candidate[field] = value
        with pytest.raises(DataChannelContractError, match=reason):
            validate_chunk(candidate)


def test_json_schemas_are_closed_and_match_golden_contracts() -> None:
    message_schema = json.loads((ROOT / "schemas/webrtc/datachannel_message.v1.json").read_text())
    chunk_schema = json.loads((ROOT / "schemas/webrtc/bounded_chunk.v1.json").read_text())
    assert not list(Draft202012Validator(message_schema).iter_errors(_message(b"golden")))
    assert not list(Draft202012Validator(chunk_schema).iter_errors(_chunk(b"golden")))
    forged = deepcopy(_message())
    forged["security"]["nonce_override"] = "forged"
    assert list(Draft202012Validator(message_schema).iter_errors(forged))
