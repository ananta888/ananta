"""Pinned clip profile execution with real Hub topology, policy and Registry."""

from unittest.mock import Mock

import pytest
from sqlalchemy import update

from agent.db_models import OrganizationInstanceDB
from agent.models.persona_media import MediaSelection
from agent.services.meet_contract import MeetError
from agent.services.meet_persona_video_profiles import MeetPersonaVideoProfiles
from agent.services.meet_persona_videos import MeetPersonaVideos
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from tests.test_meet_media import result
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_profile_service import save
from tests.test_persona_profile_service import system as system
from tests.test_persona_profile_videos import selected
from tests.test_persona_profile_videos import selected_video as selected_video
from tests.test_persona_video_asset_service import video_assets as video_assets
from tests.test_persona_video_tasks import video_task as video_task
from worker.meet_media.contract import validate_turn


@pytest.fixture
def clip_profile(request):
    case = request.getfixturevalue("selected_video")
    save(case.scope, selected(case))
    with case.scope.engine.begin() as connection:
        connection.execute(update(OrganizationInstanceDB).values(lifecycle="active"))
    case.port = MeetPersonaVideoProfiles(case.scope.service, MeetPersonaVideos(case.fixture.service))
    return case


def selection(case):
    return case.scope.service.effective(case.scope.principal, "project", "org", "organization", "org")["selection"]


def test_video_only_profile_returns_exact_clip_and_hub_binding(clip_profile):
    c = clip_profile
    pin = selection(c)
    assignment, binding = c.port.prepare(c.scope.principal, "project", pin, "preview", repeat_mode="hold_last")
    assert c.scope.service.images is None
    assert assignment["reference"] == c.asset.video.model_dump(mode="json")
    assert assignment["repeat_mode"] == "hold_last" and binding == pin
    c.port.require_current(c.scope.principal, "project", binding, assignment["reference"])


@pytest.mark.parametrize(
    "change",
    [{"selection_digest": "f" * 64}, {"organization_id": "other"}, {"owner_id": "other"}, {"permissions": ["publish"]}],
)
def test_invalid_or_stale_video_profile_never_reads_clip(clip_profile, change):
    c = clip_profile
    pin = selection(c) | change
    c.port.videos = Mock()
    with pytest.raises(MeetError):
        c.port.prepare(c.scope.principal, "project", pin, "preview", repeat_mode="loop")
    c.port.videos.prepare.assert_not_called()


@pytest.mark.parametrize("medium", ["voice", "video"])
def test_explicit_disabled_output_stops_clip_before_asset_read(clip_profile, medium):
    c = clip_profile
    value = selected(c, revision=2).model_copy(update={medium: MediaSelection(state="disabled")})
    save(c.scope, value, expected=1)
    pin = selection(c)
    c.port.videos = Mock()
    with pytest.raises(MeetError):
        c.port.prepare(c.scope.principal, "project", pin, "preview", repeat_mode="loop")
    c.port.videos.prepare.assert_not_called()


@pytest.mark.parametrize("change", ["profile", "organization", "video"])
def test_current_video_profile_rejects_revoked_or_replaced_execution(clip_profile, change):
    c = clip_profile
    pin = selection(c)
    assignment, binding = c.port.prepare(c.scope.principal, "project", pin, "preview", repeat_mode="loop")
    if change == "profile":
        save(c.scope, selected(c, revision=2), expected=1)
    elif change == "organization":
        with c.scope.engine.begin() as connection:
            connection.execute(update(OrganizationInstanceDB).values(lifecycle="paused"))
    else:
        c.fixture.service.revoke(c.scope.principal, "project", c.asset.video.artifact_id, expected_revision=2)
    with pytest.raises(MeetError):
        c.port.require_current(c.scope.principal, "project", binding, assignment["reference"])


def turn_service(case, tasks=None):
    worker = Mock()
    worker.execute.side_effect = lambda request: result() | {
        "task_id": request["task_id"],
        "lease_id": request["lease_id"],
        "persona_video": request["persona_video"]["reference"],
        "engines": result()["engines"] | {"video": "persona-clip-h264_nvenc"},
    }
    return MeetTurnService(
        Mock(),
        worker,
        tasks if tasks is not None else Mock(),
        [("tenant", "project")],
        persona_videos=case.port.videos,
        persona_video_profiles=case.port,
    ), worker


def test_video_profile_turn_persists_pin_only_on_hub_and_revokes_live_lease(clip_profile):
    c = clip_profile
    tasks = HubMediaTasks()
    service, worker = turn_service(c, tasks)
    pin = selection(c)
    respond = worker.execute.side_effect

    def execute(request):
        validate_turn(request, service.clock())
        assert "hub_persona_video_profile" not in request
        from agent.services.repository_registry import get_repository_registry

        stored = get_repository_registry().task_repo.get_by_id(request["task_id"])
        context = stored.worker_execution_context["meet_media"]
        assert context["persona_video_profile"] == pin and "persona_profile" not in context
        tasks.require_current(request | {"hub_persona_video_profile": pin})
        with pytest.raises(MeetError):
            tasks.require_current(request | {"hub_persona_video_profile": pin | {"selection_digest": "f" * 64}})
        assert service.lease_allowed(request["task_id"], request["lease_id"])
        save(c.scope, selected(c, revision=2), expected=1)
        assert not service.lease_allowed(request["task_id"], request["lease_id"])
        return respond(request)

    worker.execute.side_effect = execute
    with pytest.raises(MeetError, match="profile_denied_or_changed"):
        service.execute(
            c.scope.principal,
            "project",
            {
                "text": "Synthetic profile reply",
                "persona_video_profile": pin,
                "video_repeat_mode": "loop",
            },
        )
    worker.execute.assert_called_once()


def test_profile_clip_turn_succeeds_without_image_service_or_publication(clip_profile):
    c = clip_profile
    service, worker = turn_service(c)
    response = service.execute(
        c.scope.principal,
        "project",
        {
            "text": "Synthetic profile reply",
            "persona_video_profile": selection(c),
            "video_repeat_mode": "hold_last",
        },
    )
    assert response["persona_video"] == c.asset.video.model_dump(mode="json")
    request = worker.execute.call_args.args[0]
    assert request["persona_video"]["repeat_mode"] == "hold_last" and "meeting" not in request
    assert service.persona_images is None


@pytest.mark.parametrize(
    "extra",
    [
        {"persona_video_id": "clip"},
        {"persona_image_id": "image"},
        {"persona_profile": {}},
        {"video_repeat_mode": None},
    ],
)
def test_video_profile_cannot_mix_selectors_or_default_repeat_mode(clip_profile, extra):
    c = clip_profile
    service, worker = turn_service(c)
    with pytest.raises(MeetError):
        service.execute(
            c.scope.principal,
            "project",
            {
                "text": "Synthetic reply",
                "persona_video_profile": selection(c),
                "video_repeat_mode": "loop",
            }
            | extra,
        )
    worker.execute.assert_not_called()
    service.tasks.start.assert_not_called()
