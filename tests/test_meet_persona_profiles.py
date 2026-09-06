"""Real Hub profile/asset SQL with explicit synthetic image/worker policy fixtures."""

import json
from unittest.mock import Mock

import pytest
from sqlalchemy import update
from sqlmodel import Session

from agent.db_models import (
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
    TeamDB,
)
from agent.models.persona_media import MediaSelection
from agent.services.meet_persona_images import MeetPersonaImages
from agent.services.meet_persona_profiles import MeetPersonaProfiles
from agent.services.meet_turn_service import HubMediaTasks, MeetTurnService
from agent.services.persona_profile_images import PersonaProfileImages
from tests.test_persona_assets import application
from tests.test_persona_assets import setup as setup
from tests.test_persona_inspection_tasks import runtime as runtime
from tests.test_persona_media import profile
from tests.test_persona_profile_service import system as system
from worker.meet_media.contract import validate_turn

pytestmark = pytest.mark.timeout(45)


@pytest.fixture
def bound(request):
    profile_system = request.getfixturevalue("system")
    assets, _, kwargs = application(request.getfixturevalue("setup"))
    asset = assets.admit_image(profile_system.principal, "project", **kwargs)
    profile_system.service.images = PersonaProfileImages(assets)
    value = profile(image=MediaSelection(state="asset", asset=asset.image))
    profile_system.service.save(
        profile_system.principal, "project", "org", "organization", "org", value, expected_revision=0
    )
    with profile_system.engine.begin() as connection:
        connection.execute(update(OrganizationInstanceDB).values(lifecycle="active"))
    images = MeetPersonaImages(assets)
    profile_system.adapter = MeetPersonaProfiles(profile_system.service, images)
    profile_system.assets, profile_system.asset, profile_system.images = assets, asset, images
    return profile_system


def selection(bound, kind="organization", owner="org"):
    return bound.service.effective(bound.principal, "project", "org", kind, owner)["selection"]


def turn_service(bound, tasks=None):
    worker, tasks = Mock(), tasks or Mock()
    worker.execute.side_effect = lambda turn: {
        "task_id": turn["task_id"],
        "lease_id": turn["lease_id"],
        "persona_image": turn["persona_image"]["reference"],
    }
    return (
        MeetTurnService(
            Mock(), worker, tasks, [("tenant", "project")], persona_images=bound.images, persona_profiles=bound.adapter
        ),
        worker,
        tasks,
    )


def test_exact_profile_selection_stays_on_hub_and_worker_gets_only_closed_image_assignment(bound):
    service, worker, tasks = turn_service(bound)
    pin = selection(bound)
    result = service.execute(bound.principal, "project", {"text": "Synthetic turn", "persona_profile": pin})
    assert result["persona_image"] == bound.asset.image.model_dump(mode="json")
    wire = worker.execute.call_args.args[0]
    validate_turn(wire, service.clock())
    assert "hub_persona_profile" not in wire and "persona_profile" not in wire
    assert tasks.start.call_args.args[0]["hub_persona_profile"] == pin
    assert "meeting" not in wire


@pytest.mark.parametrize(
    "change",
    [
        {"selection_digest": "f" * 64},
        {"organization_id": "foreign"},
        {"owner_id": "foreign"},
        {"permissions": ["publish"]},
        {"owner_kind": "worker"},
    ],
)
def test_invalid_or_stale_profile_selection_never_dispatches(bound, change):
    service, worker, tasks = turn_service(bound)
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        service.execute(
            bound.principal, "project", {"text": "Synthetic turn", "persona_profile": selection(bound) | change}
        )
    worker.execute.assert_not_called()
    tasks.start.assert_not_called()


@pytest.mark.parametrize("failure", ["profile", "membership", "organization", "image"])
def test_revocation_during_media_generation_cannot_release_the_result(bound, failure):
    service, worker, tasks = turn_service(bound)
    original = worker.execute.side_effect

    def revoke(turn):
        result = original(turn)
        if failure == "profile":
            bound.repository.append(profile(revision=2), expected_revision=1, actor="actor")
        elif failure == "image":
            bound.assets.revoke(bound.principal, "project", bound.asset.image.artifact_id, expected_revision=2)
        else:
            model, values = (
                (OrganizationMembershipDB, {"expires_at": 1})
                if failure == "membership"
                else (OrganizationInstanceDB, {"lifecycle": "paused"})
            )
            with bound.engine.begin() as connection:
                connection.execute(update(model).values(**values))
        return result

    worker.execute.side_effect = revoke
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        service.execute(bound.principal, "project", {"text": "Synthetic turn", "persona_profile": selection(bound)})
    assert tasks.finish.call_args.args[1] == "failed"


def test_real_task_lease_rechecks_profile_pin_without_storing_image_bytes(bound, request):
    request.getfixturevalue("runtime")
    service, worker, tasks = turn_service(bound, HubMediaTasks())
    pin = selection(bound)

    def check_lease(turn):
        from agent.services.repository_registry import get_repository_registry

        task = get_repository_registry().task_repo.get_by_id(turn["task_id"])
        assert task.worker_execution_context["meet_media"]["persona_profile"] == pin
        assert turn["persona_image"]["png"] not in json.dumps(task.worker_execution_context)
        assert service.lease_allowed(turn["task_id"], turn["lease_id"])
        bound.repository.append(profile(revision=2), expected_revision=1, actor="actor")
        assert not service.lease_allowed(turn["task_id"], turn["lease_id"])
        return {
            "task_id": turn["task_id"],
            "lease_id": turn["lease_id"],
            "persona_image": turn["persona_image"]["reference"],
        }

    worker.execute.side_effect = check_lease
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        service.execute(bound.principal, "project", {"text": "Synthetic turn", "persona_profile": pin})


def test_profile_does_not_replace_independent_publication_policy_or_allow_mixed_selection(bound):
    service, worker, _ = turn_service(bound)
    payload = {"text": "Synthetic turn", "persona_profile": selection(bound)}
    with pytest.raises(ValueError, match="publication_disabled"):
        service.execute(bound.principal, "project", payload | {"publish_to_meet": True})
    with pytest.raises(ValueError, match="payload_invalid"):
        service.execute(bound.principal, "project", payload | {"persona_image_id": bound.asset.image.artifact_id})
    worker.execute.assert_not_called()


def test_agent_runtime_requires_active_organization_team_unit_slot_and_assignment(bound):
    TeamDB.__table__.create(bound.engine)
    with Session(bound.engine) as session:
        session.add(TeamDB(id="team", name="Synthetic team", is_active=True))
        session.commit()
    models = (OrganizationTeamLinkDB, OrganizationUnitDB, OrganizationRoleSlotDB, OrganizationRoleAssignmentDB)
    with bound.engine.begin() as connection:
        for model in models:
            connection.execute(update(model).values(lifecycle="active"))
    pin = selection(bound, "agent", "agent")
    assignment, _ = bound.adapter.prepare(bound.principal, "project", pin, "preview")
    assert assignment["reference"] == bound.asset.image.model_dump(mode="json")
    for model, field, value in [
        (TeamDB, "is_active", False),
        (OrganizationTeamLinkDB, "lifecycle", "planned"),
        (OrganizationUnitDB, "lifecycle", "planned"),
        (OrganizationRoleSlotDB, "lifecycle", "planned"),
        (OrganizationRoleAssignmentDB, "lifecycle", "suspended"),
    ]:
        with bound.engine.begin() as connection:
            connection.execute(update(model).values(**{field: value}))
        with pytest.raises(ValueError, match="profile_denied_or_changed"):
            bound.adapter.prepare(bound.principal, "project", selection(bound, "agent", "agent"), "preview")
        with bound.engine.begin() as connection:
            connection.execute(update(model).values(**{field: True if field == "is_active" else "active"}))


def test_disabled_or_missing_profile_image_never_enables_implicit_neutral_fallback(bound):
    bound.repository.append(
        profile(revision=2, image=MediaSelection(state="disabled")), expected_revision=1, actor="actor"
    )
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        bound.adapter.prepare(bound.principal, "project", selection(bound), "preview")
