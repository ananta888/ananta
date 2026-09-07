"""Deterministic metadata only: no model load, consent grant or invented evidence."""

import hashlib
import json

import pytest

from ananta_contracts.meet_voice_catalog import DEFAULT_VOICE_ID, PRESETS
from ananta_contracts.persona_voice import MEDIA_TYPE, inspect_voice_descriptor, voice_descriptor


@pytest.mark.parametrize("preset", PRESETS, ids=lambda preset: preset.voice_id)
def test_each_shipped_preset_has_one_canonical_descriptor(preset):
    content = voice_descriptor(preset.voice_id)
    inspected = inspect_voice_descriptor(content)
    assert inspected.descriptor == content
    assert inspected.voice_id == preset.voice_id
    assert inspected.source_sha256 == hashlib.sha256(content).hexdigest()
    assert len(content) < 2048


@pytest.mark.parametrize(
    "content",
    [b"", b"x" * 2049, b"null", b"[]", b"{}", b"\xff", b"[" * 1500, "{}", bytearray(b"{}")],
)
def test_malformed_or_unbounded_descriptor_is_rejected(content):
    with pytest.raises(ValueError):
        inspect_voice_descriptor(content)


@pytest.mark.parametrize("change", ["url", "path", "speaker_id", "budget", "hash", "voice", "schema", "format"])
def test_descriptor_cannot_select_paths_models_indices_or_runtime_limits(change):
    value = json.loads(voice_descriptor(DEFAULT_VOICE_ID))
    if change in ("url", "path", "speaker_id"):
        value[change] = "caller-controlled"
    elif change == "budget":
        value["speech"]["max_seconds"] = 1
    elif change == "hash":
        value["speech"]["model_sha256"] = "a" * 64
    elif change == "voice":
        value["speech"]["voice_id"] = "https://example.invalid/voice.onnx"
    elif change == "schema":
        value["schema"] = "ananta.persona-voice-preset.v2"
    else:
        value["speech"]["channels"] = True
    with pytest.raises(ValueError, match="descriptor_invalid"):
        inspect_voice_descriptor(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def test_duplicate_keys_and_noncanonical_encodings_cannot_hide_unbound_metadata():
    content = voice_descriptor(DEFAULT_VOICE_ID)
    for changed in (
        b" " + content,
        content.decode().encode("utf-16"),
        content.replace(b'{"schema":', b'{"schema":"ignored","schema":'),
    ):
        with pytest.raises(ValueError, match="descriptor_invalid"):
            inspect_voice_descriptor(changed)
    for media_type in ("application/json", "audio/wav", MEDIA_TYPE + ";charset=utf-8"):
        with pytest.raises(ValueError, match="input_invalid"):
            inspect_voice_descriptor(content, media_type)
