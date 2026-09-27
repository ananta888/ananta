"""WCRB-002/003: the request principal and the access-role admin API (headless, test app)."""

import uuid

import pytest

from agent.services import access_role_admin_service
from agent.services.access_principal import build_access_principal, identity_claims, token_roles
from agent.services.access_roles import EffectiveGrants, IdentityClaims

pytestmark = pytest.mark.timeout(60)


def test_token_roles_and_keycloak_memberships_in_both_shapes():
    ananta = {"sub": "anna", "role": "user", "idp_groups": ["/ananta/dev"], "idp_realm_roles": ["r1"],
              "idp_client_roles": ["ananta:ops"], "tenant_id": "t1"}
    raw = {"sub": "kc-subject", "groups": ["/ananta/dev"], "realm_access": {"roles": ["r1", "Offline-Access"]},
           "resource_access": {"ananta": {"roles": ["ops"]}, "account": {"roles": ["view"]}}}
    assert token_roles(ananta, is_admin=True) == {"user", "admin"}
    assert token_roles(raw, is_admin=False) == {"r1", "offline_access"}
    one, two = identity_claims(ananta), identity_claims(raw)
    assert one.username == "anna" and one.tenant_id == "t1"
    assert one.groups == two.groups == {"/ananta/dev"}
    assert one.realm_roles == {"r1"} and two.realm_roles == {"r1", "Offline-Access"}
    assert one.client_roles == {"ananta:ops"} and two.client_roles == {"ananta:ops", "account:view"}
    assert identity_claims({"sub": "agent_token"}).username == ""


def test_the_principal_carries_the_grants_of_its_bound_roles():
    seen = []

    def grants_for(claims: IdentityClaims):
        seen.append(claims)
        return EffectiveGrants(roles=frozenset({"developer"}), allow_groups=frozenset({"ananta.tool.read.v1"}))

    principal = build_access_principal({"sub": "anna", "role": "user", "project_id": "p1"}, is_admin=False,
                                       auth_source="user_jwt", grants_for=grants_for)
    assert principal.subject_id == "anna" and principal.project_id == "p1" and principal.roles == {"developer"}
    assert seen[0].username == "anna" and seen[0].project_id == "p1"
    assert principal.as_dict()["grants"]["allow_groups"] == ["ananta.tool.read.v1"]


@pytest.fixture
def fresh_service(monkeypatch):
    monkeypatch.setattr(access_role_admin_service, "_SERVICE", None)


def test_admin_api_round_trip(client, admin_auth_header, user_auth_header, fresh_service):
    role_id = "docs-" + uuid.uuid4().hex[:8]
    listed = client.get("/access/roles", headers=admin_auth_header)
    assert listed.status_code == 200
    assert {"viewer", "developer", "maintainer", "admin"} <= {row["id"] for row in listed.json["data"]["roles"]}

    saved = client.put(f"/access/roles/{role_id}", headers=admin_auth_header,
                       json={"name": "Doku", "grants": {"allow_operations": ["ananta.tool.repo.read_file_range"]}})
    assert saved.status_code == 200, saved.json
    bad = client.put("/access/roles/x!", headers=admin_auth_header, json={"grants": {}})
    assert bad.status_code == 400
    builtin = client.put("/access/roles/admin", headers=admin_auth_header, json={"grants": {}})
    assert builtin.status_code == 409

    bound = client.post("/access/bindings", headers=admin_auth_header,
                        json={"subject_kind": "local_user", "subject": "testuser", "role_id": role_id})
    assert bound.status_code == 201, bound.json
    me = client.get("/access/me", headers=user_auth_header)
    assert me.status_code == 200 and role_id in me.json["data"]["access_roles"]

    # regular users cannot administer roles
    assert client.get("/access/roles", headers=user_auth_header).status_code == 403
    assert client.post("/access/bindings", headers=user_auth_header, json={}).status_code == 403

    revisions = client.get("/access/revisions", headers=admin_auth_header).json["data"]["revisions"]
    before_binding = revisions[1]["revision"]
    rolled = client.post(f"/access/revisions/{before_binding}/rollback", headers=admin_auth_header)
    assert rolled.status_code == 200
    access_role_admin_service.get_access_role_admin_service()._cache = None
    me_again = client.get("/access/me", headers=user_auth_header)
    assert role_id not in me_again.json["data"]["access_roles"]
