"""Headless real SQL scopes/CAS; image availability is a synthetic policy port."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select, update
from sqlmodel import Session

from agent.db_models import (
    OrganizationAdminGrantDB,
    OrganizationInstanceDB,
    OrganizationMembershipDB,
    OrganizationRoleAssignmentDB,
    OrganizationTeamLinkDB,
    ProjectDB,
    ProjectMembershipDB,
)
from agent.repositories.persona_media import SqlPersonaProfiles, events, profiles
from agent.services.organization_membership_service import OrganizationMembershipService
from agent.services.persona_profile_owners import SqlPersonaProfileOwners
from agent.services.persona_profile_service import PersonaProfileService
from agent.services.project_access_authority import ProjectAccessError, SqlProjectAccessAuthority
from tests.test_persona_media import asset, profile
from tests.test_persona_media_routes import HEADERS
from tests.test_persona_media_routes import client as client

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def system(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'persona-scope.db'}")
    tables = (
        ProjectDB,
        ProjectMembershipDB,
        OrganizationInstanceDB,
        OrganizationTeamLinkDB,
        OrganizationRoleAssignmentDB,
        OrganizationMembershipDB,
        OrganizationAdminGrantDB,
    )
    for model in tables:
        model.__table__.create(engine)
    with Session(engine) as session:
        session.add(
            ProjectDB(tenant_id="tenant", project_id="project", name="Synthetic project", created_by_subject_id="actor")
        )
        session.add(ProjectMembershipDB(tenant_id="tenant", project_id="project", subject_id="actor", role="owner"))
        session.add(
            OrganizationInstanceDB(
                tenant_id="tenant",
                project_id="project",
                organization_id="org",
                name="Synthetic organization",
                definition_key="test",
                definition_version=1,
                definition_revision="d" * 64,
                effective_limit_profile_ref="test",
                effective_limit_profile_revision=1,
                effective_limit_profile_hash="a" * 64,
                composition_mode="standard",
                plan_digest="b" * 64,
                idempotency_key="test",
            )
        )
        session.add(
            OrganizationMembershipDB(
                tenant_id="tenant",
                project_id="project",
                organization_id="org",
                principal_id="actor",
                membership_kind="organization_admin",
            )
        )
        session.add(
            OrganizationAdminGrantDB(
                tenant_id="tenant",
                project_id="project",
                organization_id="org",
                principal_id="actor",
                grant_kind="persona_media",
                policy_hash="a" * 64,
                granted_by="synthetic-policy",
            )
        )
        session.add(
            OrganizationTeamLinkDB(
                tenant_id="tenant", project_id="project", organization_id="org", unit_id="unit", team_id="team"
            )
        )
        session.add(
            OrganizationRoleAssignmentDB(
                tenant_id="tenant",
                project_id="project",
                organization_id="org",
                id="agent",
                role_slot_id="slot",
                agent_url="http://synthetic-worker",
            )
        )
        session.commit()
    repository = SqlPersonaProfiles(engine)
    repository.initialize()
    images = Mock()
    service = PersonaProfileService(
        access=SqlProjectAccessAuthority(session_factory=lambda: Session(engine)),
        memberships=OrganizationMembershipService(session_factory=lambda: Session(engine)),
        owners=SqlPersonaProfileOwners(lambda: Session(engine)),
        profiles=repository,
        images=images,
    )
    principal = SimpleNamespace(
        tenant_id="tenant", project_id="project", subject_id="actor", roles={"user"}, is_admin=False
    )
    yield SimpleNamespace(service=service, principal=principal, repository=repository, engine=engine, images=images)
    engine.dispose()


def save(system, value=None, expected=0):
    value = value or profile()
    return system.service.save(
        system.principal, "project", "org", value.owner_kind, value.owner_id, value, expected_revision=expected
    )


@pytest.mark.parametrize("kind", ["organization", "team", "agent"])
def test_real_owner_scoped_profile_roundtrip_and_actor_audit(system, kind):
    value = profile(kind)
    assert system.service.current(system.principal, "project", "org", kind, value.owner_id)["revision"] == 0
    digest = save(system, value)
    result = system.service.current(system.principal, "project", "org", kind, value.owner_id)
    assert result["profile"] == value.model_dump(mode="json") and result["content_hash"] == digest
    with system.engine.connect() as connection:
        row = connection.execute(select(events)).mappings().one()
    assert row["actor"] == "actor" and row["revision"] == 1
    with pytest.raises(ValueError, match="conflict"):
        save(system, value)


@pytest.mark.parametrize(
    "model,changes",
    [
        (ProjectMembershipDB, {"state": "revoked"}),
        (OrganizationMembershipDB, {"membership_kind": "viewer"}),
        (OrganizationMembershipDB, {"expires_at": 1}),
        (OrganizationAdminGrantDB, {"revoked_at": 1}),
        (OrganizationAdminGrantDB, {"grant_kind": "unrelated"}),
        (OrganizationInstanceDB, {"lifecycle": "archived"}),
    ],
)
def test_revoked_or_insufficient_authority_never_writes(system, model, changes):
    with system.engine.begin() as connection:
        connection.execute(update(model).values(**changes))
    with pytest.raises((PermissionError, ProjectAccessError)):
        save(system)
    with system.engine.connect() as connection:
        assert connection.execute(select(profiles)).first() is None


@pytest.mark.parametrize(
    "field,value", [("tenant_id", "foreign"), ("project_id", "foreign"), ("roles", {"service"}), ("roles", {"worker"})]
)
def test_claims_cannot_broaden_user_profile_authority(system, field, value):
    setattr(system.principal, field, value)
    with pytest.raises((PermissionError, ProjectAccessError)):
        save(system)


def test_same_team_in_two_organizations_is_not_an_implicit_shared_profile_grant(system):
    with Session(system.engine) as session:
        session.add(
            OrganizationTeamLinkDB(
                tenant_id="tenant", project_id="project", organization_id="other", unit_id="other-unit", team_id="team"
            )
        )
        session.commit()
    with pytest.raises(PermissionError, match="owner_unavailable"):
        save(system, profile("team"))


def test_worker_url_is_not_an_agent_profile_owner_and_ended_assignment_is_unavailable(system):
    with pytest.raises(PermissionError):
        system.service.current(system.principal, "project", "org", "agent", "http://synthetic-worker")
    with system.engine.begin() as connection:
        connection.execute(update(OrganizationRoleAssignmentDB).values(lifecycle="ended", ended_at=1))
    with pytest.raises(PermissionError):
        save(system, profile("agent"))


def test_revoked_image_is_redacted_but_profile_can_be_cleared_without_human_repair(system):
    from agent.models.persona_media import MediaSelection

    value = profile(image=MediaSelection(state="asset", asset=asset()))
    save(system, value)
    system.images.require_reference.side_effect = PermissionError("synthetic-revocation")
    result = system.service.current(system.principal, "project", "org", "organization", "org")
    assert result["profile"] is None and result["revision"] == 1 and not result["media_available"]
    with pytest.raises(PermissionError):
        save(system, value.model_copy(update={"revision": 2}), expected=1)
    save(system, profile(revision=2, image=MediaSelection(state="disabled")), expected=1)
    assert system.service.current(system.principal, "project", "org", "organization", "org")["media_available"]


def test_profile_requests_are_not_publication_authority_and_scope_is_closed(system):
    save(system, profile(requested_usage=("publish",)))
    system.images.require_reference.assert_not_called()
    with pytest.raises(PermissionError, match="scope_mismatch"):
        system.service.save(system.principal, "project", "org", "team", "team", profile(), expected_revision=0)


def test_corrupt_head_is_not_treated_as_a_new_profile(system):
    save(system)
    with system.engine.begin() as connection:
        connection.execute(profiles.delete())
    with pytest.raises(ValueError, match="integrity_failed"):
        system.service.current(system.principal, "project", "org", "organization", "org")


def test_headless_http_profile_roundtrip_and_closed_scope(system, request):
    http, app = request.getfixturevalue("client")
    app.extensions["persona_profiles"] = system.service
    url = "/api/persona-media/v1/projects/project/organizations/org/profiles/organization/org"
    assert http.get(url, headers=HEADERS).json["revision"] == 0
    body = {"profile": profile().model_dump(mode="json"), "expected_revision": 0}
    assert http.put(url, json=body).status_code == 401
    response = http.put(url, json=body, headers=HEADERS)
    assert response.status_code == 200 and response.json["revision"] == 1
    assert http.get(url, headers=HEADERS).json["profile"] == body["profile"]
    assert http.put(url, json=body, headers=HEADERS).status_code == 409
    assert http.put(url, json=body | {"publish": True}, headers=HEADERS).status_code == 409
    assert http.get(url.replace("/org/profiles", "/foreign/profiles"), headers=HEADERS).status_code == 403
    assert http.get(url + "?tenant_id=other", headers=HEADERS).status_code == 400


def test_unsupported_media_selection_is_not_silently_accepted(system):
    from agent.models.persona_media import MediaSelection

    value = profile(voice=MediaSelection(state="asset", asset=asset(kind="voice")))
    with pytest.raises(ValueError, match="not_supported"):
        save(system, value)
