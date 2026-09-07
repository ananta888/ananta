"""Pure bounded worker codec and authority boundaries; no external model or approval."""

import base64
import time
from dataclasses import replace
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_voice_catalog import DEFAULT_VOICE_ID
from ananta_contracts.persona_inspection_wire import IMAGE_WIRE, VIDEO_WIRE, VOICE_WIRE
from ananta_contracts.persona_voice import MEDIA_TYPE, inspect_voice_descriptor, voice_descriptor
from ananta_contracts.persona_voice_wire import decode_voice, encode_voice
from worker.meet_media.persona_lease import PersonaLeaseGuard
from worker.meet_media.persona_voice_inspector import PersonaVoiceInspector


def test_voice_wire_is_separate_bounded_and_roundtrips():
    value = inspect_voice_descriptor(voice_descriptor(DEFAULT_VOICE_ID))
    assert decode_voice(encode_voice(value), value.source_sha256) == value
    assert VOICE_WIRE.path == "/v1/persona-voices"
    assert VOICE_WIRE.domain not in (IMAGE_WIRE.domain, VIDEO_WIRE.domain)
    assert VOICE_WIRE.input_limit == 2048 and VOICE_WIRE.result_limit == 4096
    for changed in (replace(value, voice_id="unknown"), replace(value, source_sha256="a" * 64)):
        with pytest.raises(ValueError):
            encode_voice(changed)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "ananta.persona-image-inspection.v1"},
        {"model_path": "/caller"},
        {"descriptor": "x" * 3000},
        {"descriptor": base64.b64encode(b"{}").decode()},
        {"descriptor": "not base64"},
        {"source_sha256": "a" * 64},
    ],
)
def test_closed_worker_result_never_accepts_mutated_or_oversized_bytes(change):
    inspected = inspect_voice_descriptor(voice_descriptor(DEFAULT_VOICE_ID))
    with pytest.raises(ValueError):
        decode_voice(encode_voice(inspected) | change, inspected.source_sha256)


@pytest.mark.parametrize("phase", [0, 1])
def test_inspector_requires_current_authority_before_and_after_validation(phase):
    guard = Mock(side_effect=[None] * phase + [PermissionError("revoked")])
    inspector = PersonaVoiceInspector(require_current=guard, deadline_monotonic=time.monotonic() + 5)
    with pytest.raises(PermissionError, match="revoked"):
        inspector.inspect(voice_descriptor(DEFAULT_VOICE_ID), MEDIA_TYPE)


def test_inspector_checks_deadline_again_after_authorization_io(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("worker.meet_media.persona_voice_inspector.time.monotonic", lambda: clock[0])

    def expired():
        clock[0] = 102

    inspector = PersonaVoiceInspector(require_current=expired, deadline_monotonic=101)
    with pytest.raises(ValueError, match="expired"):
        inspector.inspect(voice_descriptor(DEFAULT_VOICE_ID), MEDIA_TYPE)


def test_voice_lease_uses_separate_domain_and_exact_callback_path(monkeypatch):
    signed = Mock(return_value={"allowed": True})
    monkeypatch.setattr("worker.meet_media.persona_lease.signed_post", signed)
    assignment = {"deadline": int(time.time()) + 10}
    endpoint = "http://hub.test:5000/api/persona-media/v1/internal/voice-lease"
    guard = PersonaLeaseGuard(endpoint, b"test-key", assignment, kind="voice")
    guard.require()
    assert signed.call_args.args[2] == b"persona-voice-lease-v1"
    for kind in ("image", "video"):
        with pytest.raises(ValueError, match="endpoint_required"):
            PersonaLeaseGuard(endpoint, b"test-key", assignment, kind=kind)
