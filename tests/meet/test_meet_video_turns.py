"""Hub-owned clip turns and Worker renderer composition; no human approvals."""

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_media_result import validate_response_budget, validate_result
from agent.services.meet_persona_videos import MeetPersonaVideos
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from tests.test_meet_media import result, turn
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_video_asset_service import admit
from tests.test_persona_video_asset_service import video_assets as video_assets
from tests.test_persona_video_tasks import video_task as video_task
from worker.meet_media.contract import validate_turn
from worker.meet_media.persona_visual import render_visual


@pytest.fixture
def clip_turn(request):
    f = request.getfixturevalue("video_assets")
    f.asset = admit(f)
    f.port = MeetPersonaVideos(f.service)
    f.assignment = f.port.prepare(f.case.principal, "project", f.asset.video.artifact_id, "preview", repeat_mode="loop")
    return f


def runtime_for(f, *, tasks=None, issuer=None):
    worker = Mock()
    worker.execute.side_effect = lambda request: result() | {
        "task_id": request["task_id"],
        "lease_id": request["lease_id"],
        "persona_video": request["persona_video"]["reference"],
        "engines": result()["engines"] | {"video": "persona-clip-h264_nvenc"},
    }
    tasks = tasks if tasks is not None else Mock()
    if isinstance(tasks, Mock):
        tasks.finish.return_value = True
    service = MeetTurnService(
        Mock(), worker, tasks, [("tenant", "project")], persona_videos=f.port, grant_issuer=issuer
    )
    return service, worker, tasks


def payload(f, **extra):
    return {
        "text": "Synthetic clip response",
        "persona_video_id": f.asset.video.artifact_id,
        "video_repeat_mode": "loop",
    } | extra


def test_hub_turn_dispatches_closed_clip_and_verifies_result(clip_turn):
    f = clip_turn
    service, worker, tasks = runtime_for(f)
    response = service.execute(f.case.principal, "project", payload(f))
    assignment = worker.execute.call_args.args[0]
    validate_turn(assignment, service.clock())
    validate_result(response)
    validate_response_budget(assignment, response)
    assert assignment["persona_video"] == f.assignment and "meeting" not in assignment
    tasks.finish.assert_called_once_with(assignment, "completed")


@pytest.mark.parametrize("mode", [None, True, "repeat", 0])
def test_video_turn_requires_explicit_repeat_mode_before_task_creation(clip_turn, mode):
    f = clip_turn
    service, worker, tasks = runtime_for(f)
    with pytest.raises(MeetError):
        service.execute(f.case.principal, "project", payload(f, video_repeat_mode=mode))
    tasks.start.assert_not_called()
    worker.execute.assert_not_called()


@pytest.mark.parametrize("extra", [{"persona_image_id": "image"}, {"persona_profile": {}}, {"unknown": True}])
def test_clip_cannot_silently_replace_image_or_profile_selection(clip_turn, extra):
    f = clip_turn
    service, worker, tasks = runtime_for(f)
    with pytest.raises(MeetError):
        service.execute(f.case.principal, "project", payload(f, **extra))
    tasks.start.assert_not_called()
    worker.execute.assert_not_called()


def test_preview_clip_without_machine_issuer_cannot_publish(clip_turn):
    f = clip_turn
    service, worker, tasks = runtime_for(f)
    with pytest.raises(MeetError, match="publication_disabled"):
        service.execute(f.case.principal, "project", payload(f, publish_to_meet=True))
    worker.execute.assert_not_called()
    tasks.start.assert_not_called()


@pytest.mark.parametrize("boundary", ["before_dispatch", "after_generation"])
def test_revoked_clip_neither_dispatches_nor_releases_response(clip_turn, boundary):
    f = clip_turn
    service, worker, tasks = runtime_for(f)
    original = worker.execute.side_effect

    def revoke():
        f.service.revoke(f.case.principal, "project", f.asset.video.artifact_id, expected_revision=2)

    if boundary == "before_dispatch":
        tasks.start.side_effect = lambda *_: revoke()
    else:

        def execute(request):
            response = original(request)
            revoke()
            return response

        worker.execute.side_effect = execute
    with pytest.raises(MeetError):
        service.execute(f.case.principal, "project", payload(f))
    assert worker.execute.call_count == (0 if boundary == "before_dispatch" else 1)
    assert tasks.finish.call_args.args[1] == "failed"


def test_real_hub_task_stores_only_reference_and_revocation_closes_lease(clip_turn):
    f = clip_turn
    tasks = HubMediaTasks()
    service = MeetTurnService(Mock(), Mock(), tasks, [("tenant", "project")], persona_videos=f.port)
    request = turn() | {"persona_video": f.assignment}
    tasks.start(request, f.case.principal.subject_id)
    try:
        tasks.require_current(request)
        assert service.lease_allowed(request["task_id"], request["lease_id"])
        from agent.services.repository_registry import get_repository_registry

        stored = get_repository_registry().task_repo.get_by_id(request["task_id"])
        context = stored.worker_execution_context["meet_media"]
        assert context["persona_video"] == f.assignment["reference"]
        assert context["persona_video_repeat_mode"] == "loop"
        assert f.assignment["clip"]["video"] not in json.dumps(context)
        with pytest.raises(MeetError):
            tasks.require_current(request | {"persona_video": f.assignment | {"repeat_mode": "hold_last"}})
        f.service.revoke(f.case.principal, "project", f.asset.video.artifact_id, expected_revision=2)
        assert not service.lease_allowed(request["task_id"], request["lease_id"])
    finally:
        tasks.finish(request, "failed")


@pytest.mark.parametrize("change", ["missing", "hash", "wrong_engine", "both"])
def test_worker_result_cannot_claim_an_unselected_or_different_clip(clip_turn, change):
    f = clip_turn
    request = turn() | {"persona_video": f.assignment}
    response = result() | {
        "persona_video": deepcopy(f.assignment["reference"]),
        "engines": result()["engines"] | {"video": "persona-clip-h264_nvenc"},
    }
    if change == "missing":
        response.pop("persona_video")
    elif change == "hash":
        response["persona_video"]["sha256"] = "0" * 64
    elif change == "wrong_engine":
        response["engines"]["video"] = "procedural-avatar-h264_nvenc"
    else:
        response["persona_image"] = response["persona_video"] | {"kind": "image"}
    with pytest.raises(MeetError):
        validate_result(response)
        validate_response_budget(request, response)


def test_worker_clip_renderer_receives_exact_labels_mode_and_lease(clip_turn, tmp_path, monkeypatch):
    f = clip_turn
    renderer, current = Mock(return_value=tmp_path / "avatar.mp4"), Mock()

    def render(*args, **kwargs):
        from worker.meet_media.persona_video_processes import PersonaVideoProcesses

        # Exercise the real decoder deadline contract, even though this test
        # substitutes encoding. A parent's 115s budget is not a decoder budget.
        PersonaVideoProcesses(
            require_current=kwargs["require_current"], deadline_monotonic=kwargs["deadline_monotonic"], runner=Mock()
        )
        return tmp_path / "avatar.mp4"

    renderer.side_effect = render
    monkeypatch.setattr("worker.meet_media.persona_visual.render_persona_clip", renderer)
    request = turn() | {"persona_video": f.assignment}
    path, engine, reference = render_visual(request, tmp_path / "speech.wav", 1, tmp_path, require_current=current)
    assert path == tmp_path / "avatar.mp4" and engine == "persona-clip-h264_nvenc"
    assert reference == {"persona_video": f.assignment["reference"]}
    assert renderer.call_args.args[0] == f.case.inspected
    assert renderer.call_args.kwargs["repeat_mode"] == "loop"
    assert renderer.call_args.kwargs["classification"] == "test_only"
    assert renderer.call_args.kwargs["require_current"] is current
    assert current.call_count == 2


def test_local_runtime_selects_clip_renderer_without_procedural_fallback(clip_turn, monkeypatch):
    from worker.meet_media import local_runtime

    f = clip_turn
    guard = Mock()
    factory = Mock(return_value=guard)
    monkeypatch.setattr("worker.meet_media.lease_guard.HubLeaseGuard", factory)
    monkeypatch.setattr(local_runtime, "answer", lambda _: "Synthetic generated answer")
    monkeypatch.setattr(local_runtime, "PiperSpeechSource", Mock())

    def speech(_text, path, **kwargs):
        kwargs["require_current"]()
        path.write_bytes(b"RIFF0000WAVE0000")  # Explicit format fixture, not real speech.
        return [0] * 22050, 22050, 1

    def render(request, _audio, _duration, directory, *, require_current):
        require_current()
        path = directory / "clip.mp4"
        path.write_bytes(b"0000ftyp00000000")  # Explicit encoder double.
        return path, "persona-clip-h264_nvenc", {"persona_video": request["persona_video"]["reference"]}

    monkeypatch.setattr(local_runtime, "speech", speech)
    renderer = Mock(side_effect=render)
    monkeypatch.setattr("worker.meet_media.persona_visual.render_visual", renderer)
    fallback = Mock(side_effect=AssertionError("clip must not fall back to procedural avatar"))
    monkeypatch.setattr(local_runtime, "avatar", fallback)
    request = turn() | {"persona_video": f.assignment}
    response = local_runtime.run(request)
    factory.assert_called_once_with(request["task_id"], request["lease_id"], deadline=request["deadline"])
    renderer.assert_called_once()
    fallback.assert_not_called()
    assert response["persona_video"] == f.assignment["reference"] and "persona_image" not in response
    assert response["engines"]["video"] == "persona-clip-h264_nvenc"
    assert guard.require.call_count >= 4
