"""Resolved output disablement is never overridden by a bundled MP4 adapter."""

from unittest.mock import Mock

import pytest
from sqlalchemy import update
from sqlmodel import Session

from agent.db_models import (
    OrganizationRoleAssignmentDB,
    OrganizationRoleSlotDB,
    OrganizationTeamLinkDB,
    OrganizationUnitDB,
    TeamDB,
)
from agent.models.persona_media import MediaSelection, PersonaProfileSelection
from tests.test_meet_persona_profiles import bound as bound
from tests.test_meet_persona_profiles import selection, turn_service
from tests.test_persona_assets import setup as setup
from tests.test_persona_media import profile
from tests.test_persona_profile_service import system as system

pytestmark = pytest.mark.timeout(45)


def save_disabled(fixture, kind):
    value = profile(
        revision=2,
        image=MediaSelection(state="asset", asset=fixture.asset.image),
        **{kind: MediaSelection(state="disabled")},
    )
    fixture.service.save(fixture.principal, "project", "org", "organization", "org", value, expected_revision=1)


@pytest.mark.parametrize("kind", ["voice", "video"])
def test_explicit_disabled_output_rejects_bundle_before_image_load_or_worker_dispatch(request, kind):
    fixture = request.getfixturevalue("bound")
    save_disabled(fixture, kind)
    service, worker, tasks = turn_service(fixture)
    fixture.adapter.images.prepare = Mock(wraps=fixture.adapter.images.prepare)
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        service.execute(fixture.principal, "project", {"text": "synthetic", "persona_profile": selection(fixture)})
    worker.execute.assert_not_called()
    tasks.start.assert_not_called()
    fixture.adapter.images.prepare.assert_not_called()


@pytest.mark.parametrize("kind", ["voice", "video"])
def test_disabled_bundle_does_not_disable_an_independent_image_preview(request, kind):
    fixture = request.getfixturevalue("bound")
    save_disabled(fixture, kind)
    pin = PersonaProfileSelection.model_validate(selection(fixture))
    assert fixture.service.for_execution(fixture.principal, "project", pin) == fixture.asset.image.model_dump(
        mode="json"
    )
    assert fixture.service.effective(fixture.principal, "project", "org", "organization", "org")["media"][0][
        "preview_allowed"
    ]


@pytest.mark.parametrize("kind", ["voice", "video"])
def test_agent_inheritance_cannot_turn_a_disabled_organization_output_back_on(request, kind):
    fixture = request.getfixturevalue("bound")
    save_disabled(fixture, kind)
    TeamDB.__table__.create(fixture.engine)
    with Session(fixture.engine) as database:
        database.add(TeamDB(id="team", name="Synthetic team", is_active=True))
        database.commit()
    with fixture.engine.begin() as connection:
        for model in (OrganizationTeamLinkDB, OrganizationUnitDB, OrganizationRoleSlotDB, OrganizationRoleAssignmentDB):
            connection.execute(update(model).values(lifecycle="active"))
    fixture.repository.append(
        profile("agent", **{kind: MediaSelection(state="inherit")}), expected_revision=0, actor="actor"
    )
    with pytest.raises(ValueError, match="profile_denied_or_changed"):
        fixture.adapter.prepare(fixture.principal, "project", selection(fixture, "agent", "agent"), "preview")


def test_missing_voice_and_video_keep_existing_fixed_generator_compatibility(request):
    fixture = request.getfixturevalue("bound")
    assignment, _pin = fixture.adapter.prepare(fixture.principal, "project", selection(fixture), "preview")
    assert assignment["reference"] == fixture.asset.image.model_dump(mode="json")


@pytest.mark.parametrize("outputs", [(), ("microphone",), ("image", "image"), "image"])
def test_output_requirements_are_closed_and_nonempty(request, outputs):
    fixture = request.getfixturevalue("bound")
    pin = PersonaProfileSelection.model_validate(selection(fixture))
    with pytest.raises(ValueError, match="outputs_invalid"):
        fixture.service.for_execution(fixture.principal, "project", pin, required_outputs=outputs)
