"""Closed installed-handler bounds; synthetic policy, not source attestation."""

import copy
import itertools
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_dialog import validate_assignment
from ananta_contracts.meet_source_profile import CAPABILITIES, SOURCE_CLASSES, dialog_source_profile
from tests.test_meet_dialog_authority import fixture
from tests.test_meet_dialog_avatar_negotiation import system
from tests.test_meet_dialog_transport import assignment
from worker.meet_media import dialog_runtime

pytestmark = pytest.mark.timeout(45)


def test_every_nonempty_capability_subset_has_only_its_explicit_source_bounds():
    capabilities = sorted(CAPABILITIES)
    for count in range(1, len(capabilities) + 1):
        for subset in itertools.combinations(capabilities, count):
            variants = [False, True] if "avatar.publish" in subset else [False]
            for images in variants:
                profile = dialog_source_profile(subset, avatar_images=images)
                value = profile.projection()
                assert value["capabilities"] == list(subset)
                sources = value["publication_sources"]
                assert set(sources) == {
                    channel for channel in ("screen", "speech", "avatar") if channel + ".publish" in subset
                }
                if "screen" in sources:
                    assert sources["screen"] == ["agent_browser"]
                if "speech" in sources:
                    assert sources["speech"] == ["generated_audio"]
                if "avatar" in sources:
                    assert sources["avatar"] == (
                        ["generated_video", "persona_image"] if images else ["generated_video"]
                    )
                assert all(set(classes) <= SOURCE_CLASSES - {"human_device_capture"} for classes in sources.values())
                assert value["denied_operations"] == ["human_device_capture", "record", "model.train", "tool.execute"]
                profile.require_projection(copy.deepcopy(value))


@pytest.mark.parametrize(
    "caps,images",
    [
        ([], False),
        (["record"], False),
        (["tool.execute"], False),
        (["human_device_capture"], False),
        (["model.train"], False),
        (["chat.read", "chat.read"], False),
        ([{}], False),
        ("chat.read", False),
        (["chat.read"], True),
        (["avatar.publish"], 1),
        (["avatar.publish"], None),
    ],
)
def test_no_unknown_duplicate_coerced_or_unnegotiated_rights(caps, images):
    with pytest.raises(ValueError, match="meet_source_profile_invalid"):
        dialog_source_profile(caps, avatar_images=images)


def test_projection_is_immutable_and_does_not_grant_read_from_publication():
    profile = dialog_source_profile(["avatar.publish"], avatar_images=True)
    with pytest.raises(FrozenInstanceError):
        profile.capabilities = ("audio.receive",)
    value = profile.projection()
    value["publication_sources"]["avatar"].append("human_device_capture")
    value["capabilities"].append("audio.receive")
    value["denied_operations"].clear()
    assert profile.projection()["capabilities"] == ["avatar.publish"]
    with pytest.raises(ValueError, match="meet_source_profile_mismatch"):
        profile.require_projection(value)


@pytest.mark.parametrize("stored", [False, True])
def test_legacy_and_current_context_resolve_the_same_fixed_handler_without_mutating_it(stored):
    f = fixture()
    profile = dialog_source_profile(f.context["capabilities"])
    if stored:
        f.context["source_profile"] = profile.projection()
    before = copy.deepcopy(f.context)
    scope = f.authority.current("task", "dispatch", "runtime")
    assert scope.source_profile == profile and f.context == before
    assert scope.source_profile.projection()["publication_sources"] == {}


@pytest.mark.parametrize("mutation", ["missing-right", "capture", "profile", "extra", "null", "images", "denials"])
def test_current_authority_rejects_tampered_stored_projection_before_binding_read(mutation):
    f = fixture()
    value = dialog_source_profile(f.context["capabilities"]).projection()
    if mutation == "missing-right":
        value["capabilities"].remove("chat.read")
    elif mutation == "capture":
        value["publication_sources"]["screen"] = ["human_device_capture"]
    elif mutation == "profile":
        value["execution_profile"] = "personal-browser"
    elif mutation == "extra":
        value["private_path"] = "must-not-be-reported"
    elif mutation == "null":
        value = None
    elif mutation == "images":
        value["publication_sources"]["avatar"] = ["persona_image"]
    else:
        value["denied_operations"] = []
    f.context["source_profile"] = value
    with pytest.raises(MeetError, match="^meet_dialog_source_profile_denied$"):
        f.authority.current("task", "dispatch", "runtime")
    f.binding.read.assert_not_called()


@pytest.mark.parametrize("images", [False, True])
def test_actual_task_persists_hub_profile_and_preserves_closed_worker_v1_wire(app, images):
    with app.app_context():
        f = system()
        payload = f.payload | ({"avatar_images": True} if images else {})
        started = f.service.start(f.principal, "project", payload)
        task = f.tasks.get_by_id(started["task_id"])
        context = task.worker_execution_context["meet_dialog"]
        expected = dialog_source_profile(payload["capabilities"], avatar_images=images)
        assert context["source_profile"] == expected.projection()
        scope = f.f.authority.current(task.id, context["lease_id"], context["runtime_id"])
        assert scope.source_profile == expected
        wire = f.worker.start_dialog.call_args.args[0]
        assert validate_assignment(wire, f.f.now) == wire
        assert "source_profile" not in wire
        with pytest.raises(ValueError):
            validate_assignment(wire | {"source_profile": expected.projection()}, f.f.now)


@pytest.mark.parametrize("field", ["source_profile", "source_class", "execution_profile", "human_device_capture"])
def test_client_cannot_select_source_profile_before_task_ingestion(app, field):
    with app.app_context():
        f = system()
        f.tasks.start = Mock(side_effect=AssertionError("must not ingest"))
        with pytest.raises(MeetError, match="meet_dialog_start_denied"):
            f.service.start(f.principal, "project", f.payload | {field: "private-forged-profile"})
        f.tasks.start.assert_not_called()
        f.worker.start_dialog.assert_not_called()


@pytest.mark.parametrize("patch", [{"capabilities": ["human_device_capture"]}, {"avatar_images": True}])
def test_worker_fixed_handler_rejects_escalation_before_browser_launch(monkeypatch, patch):
    playwright = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr("playwright.sync_api.sync_playwright", playwright)
    with pytest.raises(ValueError, match="meet_source_profile_invalid"):
        dialog_runtime.run(assignment() | patch, Mock())
    playwright.assert_not_called()
