from __future__ import annotations

import hashlib

import pytest

from agent.services.semantic_media_program_evidence import ProgramEvidenceError, assert_content_free

CANARIES = ("KNOWN-AUDIO-CANARY", "KNOWN-TRANSCRIPT-CANARY", "KNOWN-KEY-CANARY")


@pytest.mark.parametrize("channel", ["log", "db", "task", "artifact", "metric", "browserstore"])
def test_canary_scan_rejects_plaintext_in_every_program_channel(channel: str) -> None:
    safe = {
        "channel": channel,
        "scope_digest": hashlib.sha256(channel.encode()).hexdigest(),
        "reason_code": "content_free",
        "item_count": 1,
    }
    assert_content_free(safe, known_secrets=CANARIES)
    with pytest.raises(ProgramEvidenceError, match="secret_or_unbounded_evidence_value"):
        assert_content_free({**safe, "diagnostic": CANARIES[1]}, known_secrets=CANARIES)


@pytest.mark.parametrize(
    "forbidden_field",
    ["audio", "transcript", "key_material", "payload", "local_path", "feature_vector", "prompt"],
)
def test_content_bearing_field_names_are_rejected_even_when_value_is_opaque(forbidden_field: str) -> None:
    with pytest.raises(ProgramEvidenceError, match="content_field_forbidden"):
        assert_content_free({forbidden_field: "redacted"})
