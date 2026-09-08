"""Actual Hub task start/select/exchange keeps old assignments wire-compatible."""

import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent.services.meet_contract import MeetError
from agent.services.meet_dialog_service import MeetDialogService
from agent.services.meet_dialog_tasks import HubDialogTasks
from agent.services.source_control_access_policy import HubSourcePrincipal
from ananta_contracts.meet_dialog import validate_assignment
from tests.test_meet_dialog_avatar_controls import avatar_scope
from tests.test_meet_dialog_avatar_selection import PIN, image_selection
from tests.test_meet_dialog_transport import assignment

pytestmark = pytest.mark.timeout(45)


def system(profiles=True):
    from tests.meet_dialog_lifecycle_fixture import PUBLISHER

    f, _ = avatar_scope()
    tasks = HubDialogTasks(publisher_url=PUBLISHER)
    f.authority.tasks = tasks
    issuer, worker, meet = Mock(), Mock(), Mock()
    issuer.issue_dialog.return_value = assignment()["meeting"]
    profile_service = Mock() if profiles else None
    if profile_service:
        selected = image_selection()
        profile_service.select.return_value = selected["reference"], selected["profile"]
    service = MeetDialogService(
        f.authority,
        tasks,
        meet,
        issuer,
        worker,
        Mock(),
        Mock(),
        Mock(),
        clock=lambda: f.now,
        avatar_profiles=profile_service,
    )
    principal = HubSourcePrincipal("owner", "tenant", "project", frozenset({"user"}))
    payload = {"capabilities": ["avatar.publish"], "duration_seconds": 300, "chat_mode": "off"}
    return SimpleNamespace(**locals())


@pytest.mark.parametrize("images", [False, True])
def test_actual_task_start_negotiates_only_explicit_image_support_without_activation(app, images):
    with app.app_context():
        f = system()
        payload = f.payload | ({"avatar_images": True} if images else {})
        started = f.service.start(f.principal, "project", payload)
        wire = f.worker.start_dialog.call_args.args[0]
        assert validate_assignment(wire, f.f.now) == wire
        assert (wire.get("avatar_images") is True) == images
        assert "avatar_selection" not in wire and "profile" not in wire
        status = f.service.inspect(f.principal, "project", started["task_id"])
        assert status["controls"]["avatar"]["enabled"] is False
        assert ("avatar_selection" in status) == images
        if images:
            assert status["avatar_selection"] == {"mode": "neutral-ai-v1"}
        f.profile_service.select.assert_not_called()


@pytest.mark.parametrize("flag", [False, 1, "true", None])
def test_malformed_image_negotiation_cannot_start_task(app, flag):
    with app.app_context():
        f = system()
        with pytest.raises(MeetError, match="images_invalid"):
            f.service.start(f.principal, "project", f.payload | {"avatar_images": flag})
        f.worker.start_dialog.assert_not_called()


def test_image_negotiation_requires_capability_and_configured_profile_authority(app):
    with app.app_context():
        f = system(profiles=False)
        with pytest.raises(MeetError, match="profiles_unavailable"):
            f.service.start(f.principal, "project", f.payload | {"avatar_images": True})
        f.worker.start_dialog.assert_not_called()
        for patch in ({"avatar_images": False}, {"avatar_images": True}, {"avatar_images": 1}):
            with pytest.raises(ValueError):
                validate_assignment(assignment() | patch, time.time())


def test_service_select_exchange_and_independent_avatar_revocation_are_current_hub_owned(app):
    with app.app_context():
        f = system()
        started = f.service.start(f.principal, "project", f.payload | {"avatar_images": True})
        task = started["task_id"]
        selected = f.service.select_avatar(f.principal, "project", task, {"expected_revision": 1, "profile": PIN})
        assert selected["avatar_selection"] == image_selection() and selected["controls"]["avatar"]["enabled"] is False
        active = f.service.control(
            f.principal,
            "project",
            task,
            {"expected_revision": 2, "chat": False, "audio": False, "screen": False, "avatar": True},
        )
        assert active["controls"]["avatar"]["revision"] == 3
        state = {
            "lease": {"sessionId": "ms_" + "a" * 32, "generation": 1, "expiresAt": (f.f.now + 120) * 1000},
            "peerId": "machine",
            "roomId": f.f.context["room_id"],
            "membershipEpoch": 2,
        }
        f.meet.inspect.return_value = state
        wire = f.worker.start_dialog.call_args.args[0]
        payload = {key: wire[key] for key in ("task_id", "lease_id", "runtime_id")}
        payload |= {"nonce": "a" * 32, "meet_session_id": state["lease"]["sessionId"]}
        exchange = f.service.exchange(payload)
        assert exchange["avatar"]["state"] == "ready" and exchange["avatar"]["binding"]["avatar_revision"] == 3
        f.profile_service.require_current.side_effect = PermissionError("synthetic-revocation")
        blocked = f.service.exchange(payload)
        assert blocked["avatar"] == {"mode": "persona-image-v1", "state": "blocked", "binding": None, "reference": None}
        assert blocked["controls"] == exchange["controls"]
        assert f.tasks.get_by_id(task).status == "in_progress"


def test_old_task_selection_is_denied_and_exchange_shape_remains_unchanged(app):
    with app.app_context():
        f = system()
        started = f.service.start(f.principal, "project", f.payload)
        with pytest.raises(MeetError, match="not_negotiated"):
            f.service.select_avatar(
                f.principal, "project", started["task_id"], {"expected_revision": 1, "profile": PIN}
            )
        f.meet.inspect.return_value = {"lease": {"expiresAt": (f.f.now + 120) * 1000}}
        wire = f.worker.start_dialog.call_args.args[0]
        payload = {key: wire[key] for key in ("task_id", "lease_id", "runtime_id")}
        payload |= {"nonce": "a" * 32, "meet_session_id": "ms_" + "a" * 32}
        assert set(f.service.exchange(payload)) == {
            "schema",
            "nonce",
            "authorization",
            "renewal",
            "audio_job",
            "controls",
        }
