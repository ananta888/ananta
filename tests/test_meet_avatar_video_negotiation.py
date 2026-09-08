"""Video ceilings are original assignment facts, never mutable source controls."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.common.meet_task_write_validation import meet_task_write_error
from agent.models.meet_dialog_phase import phase_binding
from agent.models.meet_preauthorization_binding import assignment_projection
from ananta_contracts.meet_avatar_video import require_video_probe
from ananta_contracts.meet_dialog import validate_assignment
from ananta_contracts.meet_source_profile import dialog_source_profile
from tests.test_meet_dialog_transport import assignment
from tests.test_meet_preauthorization_policy import context


@pytest.mark.parametrize(
    "options",
    [
        {"avatar_videos": True},
        {"avatar_videos": 1, "avatar_images": True},
        {"avatar_videos": False, "avatar_images": True},
        {"avatar_videos": None, "avatar_images": True},
    ],
)
def test_closed_worker_contract_never_infers_video_support(options):
    import time

    wire = assignment() | {"capabilities": ["avatar.publish"]} | options
    with pytest.raises(ValueError):
        validate_assignment(wire, time.time())


def test_profile_adds_only_persona_video_and_retains_every_legacy_shape():
    old = dialog_source_profile(["avatar.publish"], avatar_images=True).projection()
    new = dialog_source_profile(["avatar.publish"], avatar_images=True, avatar_videos=True).projection()
    assert new == old | {"publication_sources": {"avatar": ["generated_video", "persona_image", "persona_video"]}}
    for options in ({"avatar_videos": True}, {"avatar_images": True, "avatar_videos": 1}):
        with pytest.raises(ValueError):
            dialog_source_profile(["avatar.publish"], **options)


@pytest.mark.parametrize(
    "after",
    [
        None,
        [],
        {},
        {"meet_dialog": None},
        {"meet_dialog": {}},
        {"meet_dialog": {"avatar_videos": False}},
        {"meet_dialog": {"avatar_videos": 1}},
    ],
)
def test_active_task_option_cannot_be_removed_downgraded_or_coerced(after):
    row = SimpleNamespace(
        task_kind="meet_dialog_session",
        status="in_progress",
        worker_execution_context={"meet_dialog": {"avatar_videos": True}},
    )
    other = deepcopy(row)
    other.worker_execution_context = after
    assert meet_task_write_error(row, other) == "meet_dialog_video_negotiation_immutable"
    assert meet_task_write_error(other, row) == "meet_dialog_video_negotiation_immutable"


def test_original_preauthorization_and_phase_bind_video_but_not_selected_clip():
    before = context() | {"capabilities": ["avatar.publish"], "avatar_selection": {"mode": "neutral-ai-v1"}}
    after = before | {"avatar_videos": True}
    args = ("task", "tenant", "project")
    old = assignment_projection(*args, "https://meet.test", before)
    new = assignment_projection(*args, "https://meet.test", after)
    assert new == old | {"avatar_videos": True}
    assert phase_binding(*args, before) != phase_binding(*args, after)
    changed = after | {"avatar_selection": {"mode": "persona-video-v1", "synthetic": "passive selection"}}
    assert assignment_projection(*args, "https://meet.test", changed) == new
    assert phase_binding(*args, changed) == phase_binding(*args, after)


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"schema": "ananta.meet-avatar-video-probe.v1", "profile": "persona-video-v1", "mp4H264": 1},
        {"schema": "ananta.meet-avatar-video-probe.v1", "profile": "persona-video-v1", "mp4H264": False},
        {
            "schema": "ananta.meet-avatar-video-probe.v1",
            "profile": "persona-video-v1",
            "mp4H264": True,
            "fallback": True,
        },
    ],
)
def test_missing_malformed_or_unsupported_clip_probe_never_implies_fallback(value):
    with pytest.raises(ValueError):
        require_video_probe(value)


def test_read_only_video_probe_is_closed():
    require_video_probe({"schema": "ananta.meet-avatar-video-probe.v1", "profile": "persona-video-v1", "mp4H264": True})
