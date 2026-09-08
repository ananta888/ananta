"""Installed one-shot source bounds; no model, asset-admission or media claim."""

import copy
import time
from dataclasses import FrozenInstanceError
from unittest.mock import Mock
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.db_models import TaskDB
from agent.services.meet_contract import MeetError
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from agent.services.meet_turn_source_binding import require_source_context, source_context_matches, source_metadata
from ananta_contracts.meet_source_profile import SOURCE_CLASSES
from ananta_contracts.meet_turn_source_profile import TurnSourceProfile, profile_for_turn, turn_source_profile
from tests.test_meet_media import turn
from worker.meet_media.contract import validate_turn

pytestmark = pytest.mark.timeout(45)


@pytest.mark.parametrize("visual", ["none", "persona_image", "persona_video"])
@pytest.mark.parametrize("publish", [False, True])
def test_six_installed_variants_separate_generation_input_and_publication(visual, publish):
    profile = turn_source_profile(
        persona_image=visual == "persona_image", persona_video=visual == "persona_video", publish=publish
    )
    value = profile.projection()
    assert value["input_sources"] == ([] if visual == "none" else [visual])
    assert value["generated_sources"]["speech"] == ["generated_audio"]
    assert value["generated_sources"]["avatar"] == ["generated_video", *value["input_sources"]]
    assert set(value["generated_sources"]["avatar"]) <= SOURCE_CLASSES - {"human_device_capture"}
    assert value["publication_sources"] == (value["generated_sources"] if publish else {})
    assert value["capabilities"] == (["avatar.publish", "chat.send", "speech.publish"] if publish else [])
    assert {
        "human_device_capture",
        "record",
        "model.train",
        "tool.execute",
        "audio.receive",
        "chat.read",
        "screen.publish",
    } <= set(value["denied_operations"])
    with pytest.raises(FrozenInstanceError):
        profile.publishes = not publish
    value["generated_sources"]["avatar"].append("human_device_capture")
    assert "human_device_capture" not in profile.projection()["generated_sources"]["avatar"]
    assert "human_device_capture" not in value["publication_sources"].get("avatar", [])
    with pytest.raises(ValueError, match="mismatch"):
        profile.require_projection(value)


@pytest.mark.parametrize(
    "options",
    [{"persona_image": True, "persona_video": True}, {"publish": 1}, {"persona_image": "yes"}, {"persona_video": None}],
)
def test_ambiguous_or_coerced_renderer_selection_is_denied(options):
    with pytest.raises(ValueError, match="invalid"):
        turn_source_profile(**options)


@pytest.mark.parametrize("visual", ["human_device_capture", "browser", [], None])
def test_direct_profile_constructor_cannot_select_a_capture_runtime(visual):
    with pytest.raises(ValueError, match="invalid"):
        TurnSourceProfile(visual, False)


@pytest.mark.parametrize("field", ["source_profile", "source_class", "execution_profile", "human_device_capture"])
def test_worker_rejects_invented_profile_before_temporary_or_model_work(monkeypatch, field):
    from worker.meet_media import local_runtime

    directory = Mock(side_effect=AssertionError("must not execute"))
    monkeypatch.setattr(local_runtime.tempfile, "TemporaryDirectory", directory)
    with pytest.raises(ValueError, match="source_profile_invalid"):
        local_runtime.run(turn() | {field: "private-forged-runtime"})
    directory.assert_not_called()


@pytest.mark.parametrize(
    "mutation",
    [
        "capture",
        "capability",
        "profile",
        "inputs",
        "denials",
        "missing-profile",
        "missing-intent",
        "intent-type",
        "coherent-expansion",
    ],
)
def test_present_stored_profile_is_exact_and_cannot_broaden_original_turn(mutation):
    request = turn()
    value = source_metadata(request)
    if mutation == "capture":
        value["source_profile"]["generated_sources"]["avatar"] = ["human_device_capture"]
    elif mutation == "capability":
        value["source_profile"]["capabilities"].append("tool.execute")
    elif mutation == "profile":
        value["source_profile"]["execution_profile"] = "personal-browser"
    elif mutation == "inputs":
        value["persona_video"] = {"artifact_id": "not-admitted"}
    elif mutation == "denials":
        value["source_profile"]["denied_operations"] = []
    elif mutation == "missing-profile":
        del value["source_profile"]
    elif mutation == "missing-intent":
        del value["publication_requested"]
    elif mutation == "intent-type":
        value["publication_requested"] = 1
    else:
        value = source_metadata(request | {"meeting": {}})
    with pytest.raises(ValueError):
        require_source_context(value, request)
    assert not source_context_matches(value, request)


def change_context(engine, task_id, change):
    with Session(engine) as session:
        task = session.get(TaskDB, task_id)
        context = copy.deepcopy(task.worker_execution_context)
        change(context["meet_media"])
        task.worker_execution_context = context
        session.add(task)
        session.commit()


@pytest.mark.parametrize("publish", [False, True])
def test_real_task_queue_persists_projection_without_changing_closed_wire(app, publish):
    from agent.services.repository_registry import get_repository_registry

    request = turn() | {"task_id": str(uuid4())}
    if publish:
        request["meeting"] = {
            "origin": "https://synthetic.test",
            "room_id": "room-0123456789abcdef01",
            "grant": "a.b.c",
        }
    original = copy.deepcopy(request)
    assert validate_turn(request, time.time()) == request
    with pytest.raises(ValueError, match="contract_invalid"):
        validate_turn(request | {"source_profile": profile_for_turn(request).projection()}, time.time())
    with app.app_context():
        tasks = HubMediaTasks()
        tasks.start(request, "actor")
        task = get_repository_registry().task_repo.get_by_id(request["task_id"])
        context = task.worker_execution_context["meet_media"]
        assert context["source_profile"] == profile_for_turn(request).projection()
        assert context["publication_requested"] is publish and request == original
        tasks.require_current(request)
        assert tasks.finish(request, "completed")


@pytest.mark.parametrize("visual", ["persona_image", "persona_video"])
def test_persisted_visual_reference_determines_input_kind_without_certifying_asset_admission(visual):
    # Metadata-only fixture: actual content, licence and decoder admission are
    # deliberately not asserted by a source-classification test.
    request = turn() | {visual: {"reference": {"artifact_id": "synthetic-reference"}}}
    context = source_metadata(request) | {visual: request[visual]["reference"]}
    require_source_context(context, request)
    assert context["source_profile"]["input_sources"] == [visual]
    other = "persona_image" if visual == "persona_video" else "persona_video"
    changed = copy.deepcopy(context)
    changed[other] = changed.pop(visual)
    assert not source_context_matches(changed, request)


@pytest.mark.parametrize("legacy", [False, True])
def test_profile_tampering_blocks_capacity_success_and_lease_but_not_cleanup(app, legacy):
    from agent.database import engine

    request = turn() | {"task_id": str(uuid4())}
    with app.app_context():
        tasks = HubMediaTasks()
        tasks.start(request, "actor")

        def mutate(context):
            if legacy:
                del context["source_profile"]
                del context["publication_requested"]
            else:
                context["source_profile"]["capabilities"].append("tool.execute")

        change_context(engine, request["task_id"], mutate)
        service = MeetTurnService(Mock(), Mock(), tasks, [("tenant", "project")])
        if legacy:
            tasks.require_current(request)
            assert service.lease_allowed(request["task_id"], request["lease_id"])
            assert tasks.finish(request, "completed")
        else:
            with pytest.raises(MeetError, match="meet_capacity_task_changed"):
                tasks.require_current(request)
            assert not service.lease_allowed(request["task_id"], request["lease_id"])
            assert not tasks.finish(request, "completed")
            assert tasks.finish(request, "failed")


def test_profile_change_inside_terminal_cas_cannot_publish_success(app, monkeypatch):
    from agent.database import engine
    from agent.services import task_runtime_service

    request = turn() | {"task_id": str(uuid4())}
    native = task_runtime_service.compare_and_set_local_task_status

    def changed(*args, **kwargs):
        if kwargs.get("event_type") == "meet_media_completed":
            change_context(engine, request["task_id"], lambda context: context.update(source_profile=None))
        return native(*args, **kwargs)

    with app.app_context():
        tasks = HubMediaTasks()
        tasks.start(request, "actor")
        monkeypatch.setattr(task_runtime_service, "compare_and_set_local_task_status", changed)
        assert not tasks.finish(request, "completed")
        assert tasks.finish(request, "failed")
