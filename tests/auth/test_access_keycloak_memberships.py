"""WCRB-004: Keycloak groups, realm roles and client roles bind access roles; refreshed at every login."""

import uuid

import pytest

from agent.services import access_role_admin_service
from agent.services.access_principal import build_access_principal
from agent.services.access_roles import IdentityClaims
from agent.services.oidc_claims_mapper import idp_memberships, map_claims_to_auth

pytestmark = pytest.mark.timeout(60)

KEYCLOAK = {"sub": "kc-1", "email": "anna@example.invalid", "groups": ["/ananta/dev", "/ananta/dev", ""],
            "realm_access": {"roles": ["offline_access", "ananta-reviewer"]},
            "resource_access": {"ananta": {"roles": ["ops"]}, "account": {"roles": ["manage-account"]}}}


def test_memberships_are_read_from_every_keycloak_shape():
    assert idp_memberships(KEYCLOAK) == {
        "groups": ["/ananta/dev"], "realm_roles": ["ananta-reviewer", "offline_access"],
        "client_roles": ["account:manage-account", "ananta:ops"]}
    assert map_claims_to_auth(KEYCLOAK)["idp_memberships"]["client_roles"] == ["account:manage-account", "ananta:ops"]
    assert idp_memberships({"sub": "x", "realm_access": "bad", "groups": "bad"}) == {
        "groups": [], "realm_roles": [], "client_roles": []}
    assert len(idp_memberships({"groups": [f"/g{i}" for i in range(500)]})["groups"]) == 128


def test_stored_memberships_join_the_hub_token_identity():
    seen = []
    build_access_principal({"sub": "anna", "role": "user"}, is_admin=False, auth_source="user_jwt",
                           grants_for=lambda claims: seen.append(claims) or __import__(
                               "agent.services.access_roles", fromlist=["EffectiveGrants"]).EffectiveGrants(),
                           memberships_for=lambda username: {"groups": ["/ananta/dev"], "client_roles": ["ananta:ops"]})
    claims: IdentityClaims = seen[0]
    assert claims.username == "anna" and claims.groups == {"/ananta/dev"} and claims.client_roles == {"ananta:ops"}


def test_a_keycloak_group_binding_reaches_the_user_after_login(client, admin_auth_header, user_auth_header,
                                                               monkeypatch):
    from agent.db_models import OidcIdentityLinkDB
    from agent.repositories.auth import OidcIdentityLinkRepository

    monkeypatch.setattr(access_role_admin_service, "_SERVICE", None)
    group = "/ananta/" + uuid.uuid4().hex[:8]
    repo = OidcIdentityLinkRepository()
    issuer, subject = "https://keycloak.invalid/realms/ananta", "kc-" + uuid.uuid4().hex[:8]
    repo.save(OidcIdentityLinkDB(username="testuser", issuer=issuer, subject=subject))
    # the login hook records the memberships of this login
    assert repo.update_memberships(issuer, subject, idp_memberships({"groups": [group]}))
    bound = client.post("/access/bindings", headers=admin_auth_header,
                        json={"subject_kind": "oidc_group", "subject": group, "role_id": "maintainer"})
    assert bound.status_code == 201, bound.json
    me = client.get("/access/me", headers=user_auth_header).json["data"]
    assert "maintainer" in me["access_roles"] and group in me["groups"]

    # the next login without that group removes the role
    repo.update_memberships(issuer, subject, idp_memberships({"groups": []}))
    access_role_admin_service.get_access_role_admin_service()._cache = None
    me = client.get("/access/me", headers=user_auth_header).json["data"]
    assert "maintainer" not in me["access_roles"]
