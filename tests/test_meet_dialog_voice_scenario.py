"""Deterministic checks for explicitly synthetic live-gate policy and GPU labeling."""

from types import SimpleNamespace

import pytest

from ananta_contracts.persona_voice import inspect_voice_descriptor, voice_descriptor
from tests.meet_dialog_voice_scenario import SyntheticVoiceProfiles, make_voice_scenario


def principal(**patch):
    return SimpleNamespace(**({"subject_id": "owner", "tenant_id": "synthetic", "project_id": "synthetic"} | patch))


@pytest.mark.parametrize("name", ["neutral", "whisper"])
def test_synthetic_profile_pins_exact_descriptor_without_issuing_evidence_or_sharing_state(name):
    profiles = SyntheticVoiceProfiles()
    reference, pin, voice = profiles.catalog[name]
    result, binding = profiles.prepare(principal(), "synthetic", pin, "publish", max_seconds=7)
    assert result["speech_profile"]["voice_id"] == voice and result["speech_profile"]["max_seconds"] == 7
    assert reference["sha256"] == inspect_voice_descriptor(voice_descriptor(voice)).source_sha256
    assert reference["classification"] == "test_only"
    assert result["reference"] == reference and result["reference"] is not reference
    assert binding == pin and binding is not pin
    profiles.revoke()
    with pytest.raises(PermissionError):
        profiles.select(principal(), "synthetic", pin, "publish")


@pytest.mark.parametrize("patch", [{"subject_id": "other"}, {"tenant_id": "other"}, {"project_id": "other"}])
def test_fixture_policy_rejects_other_scope_even_with_matching_pin(patch):
    profiles = SyntheticVoiceProfiles()
    with pytest.raises(PermissionError):
        profiles.select(principal(**patch), "synthetic", profiles.catalog["neutral"][1], "publish")


@pytest.mark.parametrize(
    "enabled,worker,actual_gpu", [(False, None, False), (True, None, True), (True, object(), False)]
)
def test_live_gate_cannot_mislabel_disabled_or_synthetic_media_as_actual_gpu(monkeypatch, enabled, worker, actual_gpu):
    with pytest.raises(ValueError, match="classification_conflict"):
        make_voice_scenario(True, SimpleNamespace(enabled=enabled, worker=worker), monkeypatch, actual_gpu=actual_gpu)


def test_disabled_voice_gate_does_not_negotiate_or_modify_legacy_speech(monkeypatch):
    speech = SimpleNamespace()
    scenario = make_voice_scenario(False, speech, monkeypatch)
    assert vars(speech) == {} and scenario.profiles is None and scenario.start_options == {}
    assert scenario.finish() is False
