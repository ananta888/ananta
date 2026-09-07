"""Classify the image gate's synthetic policy/bytes without claiming admission."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ananta_contracts.meet_persona_image import decode_assignment
from tests.meet_dialog_avatar_observer import make_avatar_observer
from tests.meet_dialog_image_avatar_scenario import SyntheticImageProfiles


def principal():
    return SimpleNamespace(subject_id="owner", tenant_id="synthetic", project_id="synthetic")


def test_synthetic_image_catalog_is_closed_copied_and_binds_real_content_hashes():
    profiles = SyntheticImageProfiles()
    hashes = []
    for color in ("red", "blue"):
        pin = profiles.catalog[color][1]
        image, binding = profiles.prepare(principal(), "synthetic", pin, "publish")
        decode_assignment(image, tenant_id="synthetic", project_id="synthetic")
        assert image["reference"]["classification"] == "test_only"
        hashes.append(image["reference"]["sha256"])
        binding["owner_id"] = "mutated"
        image["reference"]["sha256"] = "0" * 64
        assert profiles.prepare(principal(), "synthetic", pin, "publish")[0]["reference"]["sha256"] == hashes[-1]
    assert len(set(hashes)) == 2


@pytest.mark.parametrize("change", ["owner", "tenant", "project", "route", "purpose", "pin", "reference", "revoked"])
def test_synthetic_policy_fixture_does_not_admit_foreign_or_revoked_bindings(change):
    profiles, subject = SyntheticImageProfiles(), principal()
    project, purpose = "synthetic", "publish"
    image, pin = profiles.prepare(subject, project, profiles.catalog["red"][1], purpose)
    if change == "owner":
        subject.subject_id = "foreign"
    elif change == "tenant":
        subject.tenant_id = "foreign"
    elif change == "project":
        subject.project_id = "foreign"
    elif change == "route":
        project = "foreign"
    elif change == "purpose":
        purpose = "preview"
    elif change == "pin":
        pin["selection_digest"] = "0" * 64
    elif change == "reference":
        image["reference"]["artifact_id"] = "foreign"
    else:
        profiles.revoke()
    with pytest.raises(PermissionError):
        profiles.require_current(subject, project, pin, image["reference"], purpose)


@pytest.mark.parametrize("mode", ["image", "image-renewal", "image-renewal-series"])
def test_synthetic_image_gate_cannot_silently_substitute_for_a_requested_gpu_case(mode, monkeypatch):
    speech = Mock(worker=None)
    with pytest.raises(ValueError, match="image_avatar_gpu_not_configured"):
        make_avatar_observer(mode, speech, monkeypatch, actual_gpu=True)
    assert speech.worker is None


@pytest.mark.parametrize("mode,seconds", [("image", None), ("image-renewal", 180), ("image-renewal-series", 360)])
def test_each_image_scenario_declares_its_own_bounded_lifetime(mode, seconds, monkeypatch):
    speech = SimpleNamespace(worker=None, capabilities=[], profile=None)
    scenario = make_avatar_observer(mode, speech, monkeypatch)
    assert scenario.start_options.get("duration_seconds") == seconds
    assert scenario.renewal_count == (3 if mode == "image-renewal-series" else 1)
    assert len(speech.capabilities) == 1 and speech.profile["max_seconds"] == 10


def test_avatar_failure_observer_is_bounded_redacted_and_does_not_change_cleanup(monkeypatch):
    from tests.meet_dialog_avatar_observer import DialogAvatarObserver
    from worker.meet_media.avatar_browser import AvatarBrowserPort
    from worker.meet_media.dialog_avatar_pump import DialogAvatarPump

    snapshot = {
        "phase": "done",
        "source": {"state": "open", "generation": 1},
        "receipt": {"private": "PRIVATE_CONTENT"},
    }
    monkeypatch.setattr(AvatarBrowserPort, "status", lambda port: snapshot)
    cleanup = Mock()
    monkeypatch.setattr(DialogAvatarPump, "_fail", cleanup)
    speech = SimpleNamespace(worker=None, capabilities=[], profile=None)
    observer = DialogAvatarObserver(True, speech, monkeypatch)
    port = AvatarBrowserPort(Mock(), "unused")
    for generation in range(1, 40):
        snapshot["source"]["generation"] = generation
        assert port.status() is snapshot
    pump = object.__new__(DialogAvatarPump)
    for _ in range(12):
        try:
            raise ValueError("meet_avatar_setup_timeout PRIVATE_CONTENT")
        except ValueError:
            pump._fail()
    report = observer.report()
    assert len(report["transitions"]) == 24
    assert report["errors"] == ["meet_avatar_setup_timeout"] * 8
    assert "PRIVATE" not in str(report)
    assert cleanup.call_count == 12
    report["errors"].clear()
    assert len(observer.report()["errors"]) == 8
