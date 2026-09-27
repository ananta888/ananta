"""WCRB-003: roles, bindings, effective grants, revisions and rollback (headless, SQLite)."""

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from agent.db_models import AccessPolicyRevisionDB, AccessRoleBindingDB, AccessRoleDB
from agent.repositories.access_roles import AccessRoleRepository
from agent.services.access_role_admin_service import AccessRoleAdminService
from agent.services.access_roles import (
    AccessRoleError,
    IdentityClaims,
    effective_grants,
    normalize_grants,
    resolve_role_ids,
)

pytestmark = pytest.mark.timeout(30)


@pytest.fixture
def service():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine, tables=[AccessRoleDB.__table__, AccessRoleBindingDB.__table__,
                                                 AccessPolicyRevisionDB.__table__])
    events = []
    clock = [0.0]
    svc = AccessRoleAdminService(AccessRoleRepository(engine), audit=lambda event, details: events.append(
        (event, details)), clock=lambda: clock[0])
    svc.events, svc.clock = events, clock
    return svc


# --- grants ---------------------------------------------------------------------------------------


def test_grants_are_validated_strictly():
    assert normalize_grants({"allow_groups": ["b", "a", "a"], "constraints": {"paths": ["agent/"]}}) == {
        "allow_groups": ["a", "b"], "constraints": {"paths": ["agent/"]}}
    for bad in ({"allow": ["x"]}, {"allow_groups": "x"}, {"admin": "yes"}, {"constraints": {"hosts": []}}, []):
        with pytest.raises(AccessRoleError):
            normalize_grants(bad)


def test_a_deny_wins_and_admin_allows_the_rest():
    roles = {"dev": {"allow_groups": ["ananta.tool.read.v1"]},
             "no-grep": {"deny_operations": ["ananta.tool.repo.grep"]},
             "boss": {"admin": True}}
    dev = effective_grants(roles, ["dev", "no-grep"])
    assert dev.allows("ananta.tool.codecompass.search", ["ananta.tool.read.v1"]) == (
        True, "allow:role:group:ananta.tool.read.v1")
    assert dev.allows("ananta.tool.repo.grep", ["ananta.tool.read.v1"])[0] is False
    assert dev.allows("ananta.tool.repo.write_file", ["ananta.tool.write.v1"]) == (False, "deny:role:no_grant")
    boss = effective_grants(roles, ["boss", "no-grep"])
    assert boss.allows("anything", []) == (True, "allow:role:admin")
    assert boss.allows("ananta.tool.repo.grep", [])[0] is False


def test_constraints_widen_with_each_role_and_vanish_with_an_unconstrained_one():
    roles = {"a": {"constraints": {"paths": ["agent/"]}}, "b": {"constraints": {"paths": ["docs/"]}}, "c": {}}
    assert effective_grants(roles, ["a", "b"]).constraints["paths"] == frozenset({"agent/", "docs/"})
    assert effective_grants(roles, ["a", "c"]).constraints["paths"] is None


def test_identities_resolve_through_every_binding_kind_and_scope():
    bindings = [
        {"subject_kind": "local_user", "subject": "anna", "role_id": "developer"},
        {"subject_kind": "oidc_group", "subject": "/ananta/maintainers", "role_id": "maintainer"},
        {"subject_kind": "oidc_realm_role", "subject": "ananta-viewer", "role_id": "viewer"},
        {"subject_kind": "oidc_client_role", "subject": "ananta:admin", "role_id": "admin"},
        {"subject_kind": "local_user", "subject": "anna", "role_id": "maintainer", "project_id": "p1"},
    ]
    assert resolve_role_ids(bindings, IdentityClaims(username="anna")) == {"developer"}
    assert resolve_role_ids(bindings, IdentityClaims(username="anna", project_id="p1")) == {"developer", "maintainer"}
    assert resolve_role_ids(bindings, IdentityClaims(groups=frozenset({"ananta/maintainers"}))) == {"maintainer"}
    assert resolve_role_ids(bindings, IdentityClaims(realm_roles=frozenset({"ananta-viewer"}))) == {"viewer"}
    assert resolve_role_ids(bindings, IdentityClaims(client_roles=frozenset({"ananta:admin"}))) == {"admin"}
    assert resolve_role_ids(bindings, IdentityClaims(username="bob")) == frozenset()


# --- admin service --------------------------------------------------------------------------------


def test_builtin_roles_exist_and_are_read_only(service):
    assert {row["id"] for row in service.snapshot()["roles"]} == {"viewer", "developer", "maintainer", "admin"}
    with pytest.raises(AccessRoleError, match="builtin_role_is_read_only"):
        service.save_role("developer", name="x", grants={}, actor="t")
    with pytest.raises(AccessRoleError, match="builtin_role_is_read_only"):
        service.delete_role("admin", actor="t")


def test_bindings_give_grants_and_every_change_is_audited_and_revisioned(service):
    service.save_role("docs-reader", name="Doku", grants={"allow_operations": ["ananta.tool.repo.read_file_range"],
                                                          "constraints": {"paths": ["docs/"]}}, actor="admin")
    binding_id, revision = service.add_binding("oidc_group", "ananta/docs", "docs-reader", actor="admin")
    assert revision == 2 and [event for event, _ in service.events] == ["access_policy_changed"] * 2
    grants = service.grants_for(IdentityClaims(groups=frozenset({"/ananta/docs"})))
    assert grants.roles == {"docs-reader"} and grants.constraints["paths"] == {"docs/"}
    with pytest.raises(AccessRoleError, match="binding_exists"):
        service.add_binding("oidc_group", "/ananta/docs", "docs-reader", actor="admin")
    with pytest.raises(AccessRoleError, match="role_not_found"):
        service.add_binding("local_user", "anna", "nope", actor="admin")
    service.delete_binding(binding_id, actor="admin")
    service.clock[0] += 60  # the resolution cache expires
    assert service.grants_for(IdentityClaims(groups=frozenset({"/ananta/docs"}))).roles == frozenset()


def test_rollback_restores_an_earlier_state_as_a_new_revision(service):
    service.save_role("ops", name="Ops", grants={"allow_groups": ["api.read.v1"]}, actor="a")
    service.add_binding("local_user", "anna", "ops", actor="a")  # revision 2
    service.delete_role("ops", actor="a")  # revision 3, binding gone too
    assert not any(row["id"] == "ops" for row in service.snapshot()["roles"])
    assert service.rollback(2, actor="a") == 4
    snapshot = service.snapshot()
    assert any(row["id"] == "ops" for row in snapshot["roles"])
    assert [row["subject"] for row in snapshot["bindings"]] == ["anna"]
    assert [row["revision"] for row in service.revisions()] == [4, 3, 2, 1]
    with pytest.raises(AccessRoleError, match="revision_not_found"):
        service.rollback(99, actor="a")


def test_binding_validation(service):
    for kind, subject in (("email", "x"), ("local_user", ""), ("oidc_client_role", "admin")):
        with pytest.raises(AccessRoleError):
            service.add_binding(kind, subject, "viewer", actor="a")
