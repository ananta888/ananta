"""Deterministic media-fixture scope, revocation, duration and call bounds."""

import copy
from types import SimpleNamespace

import pytest

from agent.services.meet_speech_result import validate_speech_binding
from ananta_contracts.meet_persona_image import decode_assignment
from ananta_contracts.meet_speech import speech_profile
from tests.meet_dialog_interruption import SyntheticToneWorker
from tests.meet_multi_worker_media import IndependentlyRevocableImages, MultiWorkerMediaScenario, TwoReplyToneWorker

pytestmark = pytest.mark.timeout(45)


def principal():
    return SimpleNamespace(subject_id="owner", tenant_id="synthetic", project_id="synthetic")


def test_initial_personas_are_passive_copied_and_exactly_bound_to_each_worker_index():
    scenario = MultiWorkerMediaScenario()
    first, second = scenario.initial_options(0), scenario.initial_options(1)
    assert first["initial_persona"]["avatar"]["profile"] == scenario.images.pin("red")
    assert second["initial_persona"]["avatar"]["profile"] == scenario.images.pin("blue")
    first["initial_persona"]["avatar"]["profile"]["owner_id"] = "foreign"
    assert scenario.initial_options(0)["initial_persona"]["avatar"]["profile"] == scenario.images.pin("red")
    assert scenario.worker.calls == [] and "controls" not in first
    for invalid in (True, 0.0, -1, 2, "0"):
        with pytest.raises(ValueError, match="index_invalid"):
            scenario.initial_options(invalid)


def test_images_are_distinct_copied_test_only_and_independently_revocable():
    catalog = IndependentlyRevocableImages()
    first, second = catalog.pin("red"), catalog.pin("blue")
    assert first["owner_id"] != second["owner_id"] and first["selection_digest"] != second["selection_digest"]
    for pin in (first, second):
        image, bound = catalog.prepare(principal(), "synthetic", pin, "publish")
        decode_assignment(image, tenant_id="synthetic", project_id="synthetic")
        assert image["reference"]["classification"] == "test_only"
        bound["owner_id"] = "changed"
        assert catalog.prepare(principal(), "synthetic", pin, "publish")[1] == pin
    catalog.revoke("red")
    catalog.revoke("red")
    with pytest.raises(PermissionError, match="revoked"):
        catalog.prepare(principal(), "synthetic", first, "publish")
    image, pin = catalog.prepare(principal(), "synthetic", second, "publish")
    catalog.require_current(principal(), "synthetic", pin, image["reference"], "publish")


@pytest.mark.parametrize(
    "change", ["tenant", "project", "actor", "organization", "owner", "digest", "revision", "artifact", "purpose"]
)
def test_foreign_or_mutated_media_bindings_fail_closed(change):
    catalog = IndependentlyRevocableImages()
    actor = principal()
    image, pin = catalog.prepare(actor, "synthetic", catalog.pin("red"), "publish")
    reference, purpose = copy.deepcopy(image["reference"]), "publish"
    if change == "tenant":
        actor.tenant_id = "foreign"
    elif change == "project":
        actor.project_id = "foreign"
    elif change == "actor":
        actor.subject_id = "foreign"
    elif change == "organization":
        pin["organization_id"] = "foreign"
    elif change == "owner":
        pin["owner_id"] = "foreign"
    elif change == "digest":
        pin["selection_digest"] = "0" * 64
    elif change == "revision":
        reference["revision"] += 1
    elif change == "artifact":
        reference["artifact_id"] = "foreign"
    else:
        purpose = "preview"
    with pytest.raises(PermissionError):
        catalog.require_current(actor, "synthetic", pin, reference, purpose)


@pytest.mark.parametrize("seconds", [0, 1, 21, 60, "20", True])
def test_tone_cannot_expand_the_two_explicit_duration_profiles(seconds):
    with pytest.raises(ValueError, match="duration_invalid"):
        SyntheticToneWorker(seconds=seconds)


def test_two_reply_fixture_keeps_exact_twenty_second_pcm_and_bounded_ownership():
    worker = TwoReplyToneWorker()
    turn = {
        "task_id": "child",
        "lease_id": "lease",
        "binding_task_id": "first",
        "speech_profile": speech_profile(max_seconds=20),
    }
    result = worker.execute(turn)
    validate_speech_binding(turn, result)
    assert result["speech"]["samples"] == 441000 and result["duration_seconds"] == 20
    worker.execute(turn | {"binding_task_id": "second"})
    with pytest.raises(ValueError, match="reply_budget"):
        worker.execute(turn)
    assert [call[0] for call in worker.calls] == ["first", "second"]
