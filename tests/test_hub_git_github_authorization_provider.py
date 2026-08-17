from __future__ import annotations

import json

import pytest

from agent.services.hub_git_authorization_provisioning import (
    GitAuthorizationProvisioningRequest,
    GitAuthorizationSelection,
    HubGitAuthorizationProvisioningError,
    UnavailableHubGitSecretResolver,
)
from agent.services.hub_git_github_authorization_provider import (
    ComposedHubGitSecretResolver,
    GitHubAppInstallationSecretResolver,
    GitHubAuthorizationProvisioner,
    HttpGitHubAuthorizationApi,
    UnavailableGitHubOAuthGrantStore,
    compose_github_authorization_provisioner_from_env,
)
from agent.sources.git_source_connector_common import GitSourceScope


class _Jwt:
    def issue(self) -> str:
        return "app-jwt"


class _Api:
    def __init__(self) -> None:
        self.installation = {
            "id": 42,
            "account": {"login": "owner", "type": "Organization"},
            "permissions": {"contents": "read"},
        }
        self.token = {
            "token": "ghs_should-never-escape",
            "permissions": {"contents": "read"},
        }
        self.repository = {"full_name": "owner/repository"}
        self.oauth_scopes = frozenset({"repo"})
        self.token_calls = 0

    def inspect_installation(self, *, installation_id, app_jwt):
        assert installation_id == "42"
        assert app_jwt == "app-jwt"
        return self.installation

    def create_installation_token(self, *, installation_id, app_jwt):
        assert installation_id == "42"
        assert app_jwt == "app-jwt"
        self.token_calls += 1
        return self.token

    def inspect_repository(self, *, repository, access_token):
        assert repository == "owner/repository"
        assert access_token
        assert "ghs_" in access_token or access_token == "oauth-token"
        return self.repository

    def inspect_oauth_scopes(self, *, access_token):
        assert access_token == "oauth-token"
        return self.oauth_scopes


class _OAuthStore:
    def resolve_token(self, handle):
        assert handle == "github-oauth:user-1"
        return "oauth-token"


def _scope():
    return GitSourceScope(
        tenant_id="tenant-1",
        project_id="project-1",
        owner_id="owner-1",
    )


def _request(*, kind="github_app", handle="github-installation:42", repository="owner/repository"):
    return GitAuthorizationProvisioningRequest(
        scope=_scope(),
        selection=GitAuthorizationSelection(
            authorization_handle=handle,
            authorization_kind=kind,
            repository=repository,
        ),
    )


def test_github_app_resolves_org_repo_without_returning_tokens():
    api = _Api()
    provisioner = GitHubAuthorizationProvisioner(api=api, jwt_issuer=_Jwt())

    resolved = provisioner.resolve_authorization(_request())
    encoded = json.dumps(
        {
            "connection_ref": resolved.connection_ref,
            "kind": resolved.authorization_kind,
            "state": resolved.authorization_state,
            "scopes": sorted(resolved.granted_scopes),
            "repository": resolved.repository,
            "credential_ref": resolved.credential_ref,
        }
    )

    assert resolved.authorization_kind == "github_app"
    assert resolved.authorization_state == "active"
    assert resolved.granted_scopes == frozenset({"contents:read"})
    assert resolved.repository == "owner/repository"
    assert resolved.remote_url == "https://github.com/owner/repository.git"
    assert resolved.credential_ref == "secret://github-app/installation/42"
    assert "ghs_should-never-escape" not in encoded
    assert "ghs_should-never-escape" not in repr(resolved)
    assert provisioner.health(scope=_scope()).status == "healthy"


def test_github_app_rejects_missing_contents_permission():
    api = _Api()
    api.token["permissions"] = {"metadata": "read"}
    provisioner = GitHubAuthorizationProvisioner(api=api, jwt_issuer=_Jwt())

    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_required_scope_missing",
    ):
        provisioner.resolve_authorization(_request())


def test_github_app_rejects_repository_outside_installation():
    api = _Api()
    api.repository = {"full_name": "other/repo"}
    provisioner = GitHubAuthorizationProvisioner(api=api, jwt_issuer=_Jwt())

    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_repository_not_granted",
    ):
        provisioner.resolve_authorization(_request())


def test_github_oauth_uses_stored_grant_and_least_privilege_scopes():
    provisioner = GitHubAuthorizationProvisioner(
        api=_Api(),
        oauth_grants=_OAuthStore(),
    )

    resolved = provisioner.resolve_authorization(
        _request(kind="github_oauth", handle="github-oauth:user-1")
    )

    assert resolved.authorization_kind == "github_oauth"
    assert resolved.granted_scopes == frozenset({"contents:read"})
    assert resolved.credential_ref == "secret://github-oauth/grant/user-1"
    assert "oauth-token" not in repr(resolved)


def test_github_oauth_fails_closed_without_grant_store():
    provisioner = GitHubAuthorizationProvisioner(api=_Api())

    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_oauth_grant_unavailable",
    ):
        provisioner.resolve_authorization(
            _request(kind="github_oauth", handle="github-oauth:user-1")
        )


def test_github_oauth_rejects_insufficient_scopes():
    api = _Api()
    api.oauth_scopes = frozenset({"read:user"})
    provisioner = GitHubAuthorizationProvisioner(
        api=api,
        oauth_grants=_OAuthStore(),
    )

    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_required_scope_missing",
    ):
        provisioner.resolve_authorization(
            _request(kind="github_oauth", handle="github-oauth:user-1")
        )


def test_secret_resolver_mints_installation_token_on_demand():
    api = _Api()
    resolver = GitHubAppInstallationSecretResolver(api=api, jwt_issuer=_Jwt())

    token = resolver.resolve("secret://github-app/installation/42")

    assert token == "ghs_should-never-escape"
    assert api.token_calls == 1
    assert resolver.handles("secret://github-app/installation/42")
    assert not resolver.handles("secret://other/ref")


def test_composed_secret_resolver_keeps_fallback_fail_closed():
    resolver = ComposedHubGitSecretResolver(
        github=GitHubAppInstallationSecretResolver(api=_Api(), jwt_issuer=_Jwt()),
        fallback=UnavailableHubGitSecretResolver(),
    )

    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_secret_resolver_unavailable",
    ):
        resolver.resolve("secret://unrelated/ref")


def test_http_api_rejects_non_github_origins():
    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_github_api_origin_invalid",
    ):
        HttpGitHubAuthorizationApi(api_origin="https://evil.example/api")


def test_factory_stays_unconfigured_without_app_credentials(monkeypatch):
    monkeypatch.delenv("HUB_GIT_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("HUB_GIT_GITHUB_APP_PRIVATE_KEY_REF", raising=False)

    assert (
        compose_github_authorization_provisioner_from_env(
            secret_resolver=UnavailableHubGitSecretResolver(),
        )
        is None
    )


def test_unavailable_oauth_store_is_explicit():
    with pytest.raises(
        HubGitAuthorizationProvisioningError,
        match="git_authorization_oauth_grant_unavailable",
    ):
        UnavailableGitHubOAuthGrantStore().resolve_token("github-oauth:user-1")
