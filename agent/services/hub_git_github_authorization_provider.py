"""GitHub App and OAuth adapters for Hub Git authorization provisioning.

The browser still sends only an opaque handle and repository.  This adapter
talks to GitHub from the Hub, stores no access tokens in the registration, and
mints installation tokens only when the secret resolver is later invoked.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from urllib.parse import quote, unquote, urlsplit

from agent.services.hub_git_authorization_provisioning import (
    GitAuthorizationProviderHealth,
    GitAuthorizationProvisioningRequest,
    HubGitAuthorizationProvisioningError,
    ProvisionedGitAuthorization,
)
from agent.sources.git_source_connector_common import GitSourceScope

_INSTALLATION_HANDLE = re.compile(r"^github-installation:(\d{1,20})$")
_OAUTH_HANDLE = re.compile(
    r"^github-oauth:([A-Za-z0-9][A-Za-z0-9_.:-]{0,191})$"
)
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_DEFAULT_API_ORIGIN = "https://api.github.com"
_ALLOWED_API_HOSTS = frozenset({"api.github.com"})
_CONTENTS_PERMISSIONS = frozenset({"read", "write"})
_OAUTH_CONTENT_SCOPES = frozenset(
    {"contents:read", "repo", "public_repo"}
)
_MAX_BODY_BYTES = 64 * 1024


class GitHubAuthorizationApiPort(Protocol):
    def inspect_installation(
        self, *, installation_id: str, app_jwt: str
    ) -> Mapping[str, Any]: ...

    def create_installation_token(
        self, *, installation_id: str, app_jwt: str, repository: str
    ) -> Mapping[str, Any]: ...

    def inspect_repository(
        self, *, repository: str, access_token: str
    ) -> Mapping[str, Any]: ...

    def inspect_oauth_scopes(self, *, access_token: str) -> frozenset[str]: ...


class GitHubAppJwtIssuerPort(Protocol):
    def issue(self) -> str: ...


class GitHubOAuthGrantStorePort(Protocol):
    def resolve_token(self, handle: str) -> str: ...


class GitHubAppJwtIssuer:
    """Issue a short-lived RS256 App JWT from a PEM already resolved in Hub."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key_pem: str,
        clock=time.time,
    ) -> None:
        self._app_id = str(app_id or "").strip()
        self._private_key_pem = str(private_key_pem or "")
        self._clock = clock
        if not self._app_id.isdigit() or not self._private_key_pem.strip():
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_app_credentials_invalid",
                status_code=503,
            )

    def issue(self) -> str:
        import jwt

        now = int(self._clock())
        return jwt.encode(
            {
                "iat": now - 60,
                "exp": now + 540,
                "iss": self._app_id,
            },
            self._private_key_pem,
            algorithm="RS256",
        )


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


class HttpGitHubAuthorizationApi:
    """Bounded GitHub REST client used only by the Hub provisioner."""

    def __init__(self, *, api_origin: str = _DEFAULT_API_ORIGIN) -> None:
        parsed = urlsplit(str(api_origin or "").strip())
        host = str(parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or host not in _ALLOWED_API_HOSTS
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_api_origin_invalid",
                status_code=503,
            )
        self._origin = f"https://{host}"
        self._opener = urllib.request.build_opener(NoRedirectHandler)

    def inspect_installation(
        self, *, installation_id: str, app_jwt: str
    ) -> Mapping[str, Any]:
        return self._json(
            "GET",
            f"/app/installations/{quote(installation_id, safe='')}",
            bearer=app_jwt,
        )

    def create_installation_token(
        self, *, installation_id: str, app_jwt: str, repository: str
    ) -> Mapping[str, Any]:
        repository_name = _require_repository(repository).split("/", 1)[1]
        return self._json(
            "POST",
            f"/app/installations/{quote(installation_id, safe='')}/access_tokens",
            bearer=app_jwt,
            body={
                "repositories": [repository_name],
                "permissions": {"contents": "read"},
            },
        )

    def inspect_repository(
        self, *, repository: str, access_token: str
    ) -> Mapping[str, Any]:
        return self._json(
            "GET",
            f"/repos/{quote(repository, safe='/')}",
            bearer=access_token,
        )

    def inspect_oauth_scopes(self, *, access_token: str) -> frozenset[str]:
        headers, _payload = self._request(
            "GET",
            "/user",
            bearer=access_token,
        )
        raw = str(headers.get("X-OAuth-Scopes") or "")
        return frozenset(
            item.strip() for item in raw.split(",") if item.strip()
        )

    def _json(
        self, method: str, path: str, *, bearer: str, body: Mapping[str, Any] | None = None
    ) -> Mapping[str, Any]:
        _headers, payload = self._request(method, path, bearer=bearer, body=body)
        if not isinstance(payload, Mapping):
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_response_invalid",
                status_code=503,
            )
        return dict(payload)

    def _request(
        self, method: str, path: str, *, bearer: str, body: Mapping[str, Any] | None = None
    ) -> tuple[Mapping[str, str], Any]:
        token = str(bearer or "").strip()
        if not token:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_credential_missing",
                status_code=503,
            )
        encoded_body = (
            json.dumps(dict(body), separators=(",", ":")).encode("utf-8")
            if body is not None
            else None
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "ananta-hub-git-authorization",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if encoded_body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._origin}{path}",
            method=method,
            headers=headers,
            data=encoded_body,
        )
        try:
            with self._opener.open(request, timeout=10) as response:
                raw = response.read(_MAX_BODY_BYTES + 1)
                headers = {str(key): str(value) for key, value in response.headers.items()}
        except urllib.error.HTTPError as exc:
            raise HubGitAuthorizationProvisioningError(
                _http_reason(exc.code),
                status_code=503,
            ) from None
        except urllib.error.URLError:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_unreachable",
                status_code=503,
            ) from None
        if len(raw) > _MAX_BODY_BYTES:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_response_too_large",
                status_code=503,
            )
        if not raw:
            return headers, {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_response_invalid",
                status_code=503,
            ) from None
        return headers, decoded


class UnavailableGitHubOAuthGrantStore:
    def resolve_token(self, handle: str) -> str:
        del handle
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_oauth_grant_unavailable",
            status_code=503,
        )


def _assert_installation_active(installation: Mapping[str, Any]) -> None:
    state = str(installation.get("status") or installation.get("state") or "").strip().lower()
    if (
        installation.get("suspended_at")
        or installation.get("suspended_by")
        or installation.get("deleted_at")
        or state in {"deleted", "disabled", "inactive", "suspended"}
    ):
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_github_installation_inactive",
            status_code=403,
        )


def migrate_legacy_github_app_reference(
    reference: str,
    *,
    repository: str | None = None,
    dry_run: bool = True,
) -> dict[str, str | bool]:
    """Convert an installation-only legacy ref or explicitly invalidate it."""

    value = str(reference or "").strip()
    prefix = "secret://github-app/installation/"
    if not value.startswith(prefix):
        return {"status": "not_applicable", "reference": value, "changed": False}
    remainder = value.removeprefix(prefix)
    installation_id, separator, encoded_repository = remainder.partition("/repository/")
    if not installation_id.isdigit():
        return {"status": "invalidated", "reference": "", "changed": True}
    if separator and unquote(encoded_repository):
        return {"status": "current", "reference": value, "changed": False}
    if not repository:
        return {"status": "invalidated", "reference": "", "changed": True}
    scoped = (
        f"{prefix}{installation_id}/repository/"
        f"{quote(_require_repository(repository), safe='')}"
    )
    return {
        "status": "planned" if dry_run else "migrated",
        "reference": scoped,
        "changed": True,
    }


@dataclass(frozen=True)
class GitHubAuthorizationProvisioner:
    """Resolve GitHub App installations and stored OAuth grants."""

    api: GitHubAuthorizationApiPort
    jwt_issuer: GitHubAppJwtIssuerPort | None = None
    oauth_grants: GitHubOAuthGrantStorePort | None = None

    def resolve_authorization(
        self, request: GitAuthorizationProvisioningRequest
    ) -> ProvisionedGitAuthorization:
        selection = request.selection
        if selection.authorization_kind == "github_app":
            return self._resolve_app(selection.authorization_handle, selection.repository)
        if selection.authorization_kind == "github_oauth":
            return self._resolve_oauth(selection.authorization_handle, selection.repository)
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_kind_unsupported",
            status_code=400,
        )

    def health(self, *, scope: GitSourceScope) -> GitAuthorizationProviderHealth:
        _ = scope
        if self.jwt_issuer is None:
            return GitAuthorizationProviderHealth(
                status="unavailable",
                reason_code="git_authorization_github_app_unconfigured",
            )
        try:
            self.jwt_issuer.issue()
        except Exception:
            return GitAuthorizationProviderHealth(
                status="unavailable",
                reason_code="git_authorization_github_app_credentials_invalid",
            )
        return GitAuthorizationProviderHealth(status="healthy")

    def _resolve_app(
        self, handle: str, repository: str | None
    ) -> ProvisionedGitAuthorization:
        match = _INSTALLATION_HANDLE.fullmatch(str(handle or "").strip())
        repo = _require_repository(repository)
        if match is None or self.jwt_issuer is None:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_app_handle_invalid"
                if match is None
                else "git_authorization_github_app_unconfigured",
                status_code=400 if match is None else 503,
            )
        installation_id = match.group(1)
        app_jwt = self.jwt_issuer.issue()
        installation = self.api.inspect_installation(
            installation_id=installation_id,
            app_jwt=app_jwt,
        )
        _assert_installation_active(installation)
        token_payload = self.api.create_installation_token(
            installation_id=installation_id,
            app_jwt=app_jwt,
            repository=repo,
        )
        access_token = str(token_payload.get("token") or "").strip()
        permissions = token_payload.get("permissions")
        if not isinstance(permissions, Mapping):
            permissions = installation.get("permissions")
        if not isinstance(permissions, Mapping):
            permissions = {}
        contents = str(permissions.get("contents") or "").strip().lower()
        if contents not in _CONTENTS_PERMISSIONS:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_required_scope_missing",
                status_code=403,
            )
        if not access_token:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_credential_missing",
                status_code=503,
            )
        inspected = self.api.inspect_repository(
            repository=repo,
            access_token=access_token,
        )
        _assert_repository_match(inspected, repo)
        return ProvisionedGitAuthorization(
            connection_ref=handle,
            authorization_kind="github_app",
            remote_url=f"https://github.com/{repo}.git",
            credential_ref=(
                f"secret://github-app/installation/{installation_id}/repository/"
                f"{quote(repo, safe='')}"
            ),
            credential_username="x-access-token",
            authorization_state="active",
            granted_scopes=frozenset({"contents:read"}),
            repository=repo,
        )

    def _resolve_oauth(
        self, handle: str, repository: str | None
    ) -> ProvisionedGitAuthorization:
        match = _OAUTH_HANDLE.fullmatch(str(handle or "").strip())
        repo = _require_repository(repository)
        store = self.oauth_grants or UnavailableGitHubOAuthGrantStore()
        if match is None:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_github_oauth_handle_invalid"
            )
        access_token = str(store.resolve_token(handle) or "").strip()
        if not access_token:
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_oauth_grant_unavailable",
                status_code=503,
            )
        scopes = self.api.inspect_oauth_scopes(access_token=access_token)
        if scopes.isdisjoint(_OAUTH_CONTENT_SCOPES):
            raise HubGitAuthorizationProvisioningError(
                "git_authorization_required_scope_missing",
                status_code=403,
            )
        inspected = self.api.inspect_repository(
            repository=repo,
            access_token=access_token,
        )
        _assert_repository_match(inspected, repo)
        grant = match.group(1)
        return ProvisionedGitAuthorization(
            connection_ref=handle,
            authorization_kind="github_oauth",
            remote_url=f"https://github.com/{repo}.git",
            credential_ref=(
                f"secret://github-oauth/grant/{grant}/repository/"
                f"{quote(repo, safe='')}"
            ),
            credential_username="x-access-token",
            authorization_state="active",
            granted_scopes=frozenset({"contents:read"}),
            repository=repo,
        )


class GitHubAppInstallationSecretResolver:
    """Mint a short-lived installation token from an opaque credential ref."""

    def __init__(
        self,
        *,
        api: GitHubAuthorizationApiPort,
        jwt_issuer: GitHubAppJwtIssuerPort,
        oauth_grants: GitHubOAuthGrantStorePort | None = None,
    ) -> None:
        self._api = api
        self._jwt_issuer = jwt_issuer
        self._oauth_grants = oauth_grants or UnavailableGitHubOAuthGrantStore()

    def handles(self, reference: str) -> bool:
        value = str(reference or "").strip()
        return value.startswith("secret://github-app/installation/") or value.startswith(
            "secret://github-oauth/grant/"
        )

    def resolve(self, reference: str) -> str:
        value = str(reference or "").strip()
        prefix = "secret://github-app/installation/"
        if value.startswith(prefix):
            remainder = value.removeprefix(prefix)
            installation_id, separator, encoded_repository = remainder.partition("/repository/")
            repository = unquote(encoded_repository) if separator else ""
            if not installation_id.isdigit() or not repository:
                raise HubGitAuthorizationProvisioningError(
                    "git_secret_reference_invalid",
                    status_code=503,
                )
            app_jwt = self._jwt_issuer.issue()
            installation = self._api.inspect_installation(
                installation_id=installation_id,
                app_jwt=app_jwt,
            )
            _assert_installation_active(installation)
            token_payload = self._api.create_installation_token(
                installation_id=installation_id,
                app_jwt=app_jwt,
                repository=_require_repository(repository),
            )
            token = str(token_payload.get("token") or "").strip()
            if not token:
                raise HubGitAuthorizationProvisioningError(
                    "git_authorization_github_credential_missing",
                    status_code=503,
                )
            return token
        oauth_prefix = "secret://github-oauth/grant/"
        if value.startswith(oauth_prefix):
            remainder = value.removeprefix(oauth_prefix)
            grant, separator, encoded_repository = remainder.partition("/repository/")
            repository = unquote(encoded_repository) if separator else ""
            if not grant or not repository:
                raise HubGitAuthorizationProvisioningError(
                    "git_secret_reference_invalid", status_code=503
                )
            handle = f"github-oauth:{grant}"
            token = str(self._oauth_grants.resolve_token(handle) or "").strip()
            if not token:
                raise HubGitAuthorizationProvisioningError(
                    "git_authorization_oauth_grant_unavailable",
                    status_code=503,
                )
            inspected = self._api.inspect_repository(
                repository=_require_repository(repository),
                access_token=token,
            )
            _assert_repository_match(inspected, repository)
            return token
        raise HubGitAuthorizationProvisioningError(
            "git_secret_resolver_unavailable",
            status_code=503,
        )


class ComposedHubGitSecretResolver:
    def __init__(
        self,
        *,
        github: GitHubAppInstallationSecretResolver,
        fallback: Any,
    ) -> None:
        self._github = github
        self._fallback = fallback

    def resolve(self, reference: str) -> str:
        if self._github.handles(reference):
            return self._github.resolve(reference)
        return self._fallback.resolve(reference)


def compose_github_authorization_provisioner_from_env(
    *,
    secret_resolver: Any,
    oauth_grants: GitHubOAuthGrantStorePort | None = None,
) -> tuple[GitHubAuthorizationProvisioner, Any] | None:
    """Return the GitHub adapter only when App credentials are already configured."""

    app_id = str(os.environ.get("HUB_GIT_GITHUB_APP_ID") or "").strip()
    key_ref = str(os.environ.get("HUB_GIT_GITHUB_APP_PRIVATE_KEY_REF") or "").strip()
    if not app_id or not key_ref:
        return None
    try:
        private_key = secret_resolver.resolve(key_ref)
        issuer = GitHubAppJwtIssuer(app_id=app_id, private_key_pem=private_key)
        api = HttpGitHubAuthorizationApi()
    except Exception:
        return None
    provisioner = GitHubAuthorizationProvisioner(
        api=api,
        jwt_issuer=issuer,
        oauth_grants=oauth_grants,
    )
    github_secrets = GitHubAppInstallationSecretResolver(
        api=api,
        jwt_issuer=issuer,
        oauth_grants=oauth_grants,
    )
    return provisioner, ComposedHubGitSecretResolver(
        github=github_secrets,
        fallback=secret_resolver,
    )


def _require_repository(repository: str | None) -> str:
    value = str(repository or "").strip()
    if _REPOSITORY.fullmatch(value) is None:
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_repository_invalid"
        )
    return value


def _assert_repository_match(payload: Mapping[str, Any], repository: str) -> None:
    full_name = str(payload.get("full_name") or "").strip()
    if full_name.lower() != repository.lower():
        raise HubGitAuthorizationProvisioningError(
            "git_authorization_repository_not_granted",
            status_code=403,
        )


def _http_reason(status_code: int) -> str:
    if int(status_code) in {401, 403}:
        return "git_authorization_github_denied"
    if int(status_code) == 404:
        return "git_authorization_github_not_found"
    return "git_authorization_github_unavailable"


__all__ = [
    "ComposedHubGitSecretResolver",
    "GitHubAppInstallationSecretResolver",
    "GitHubAppJwtIssuer",
    "GitHubAuthorizationApiPort",
    "GitHubAuthorizationProvisioner",
    "GitHubOAuthGrantStorePort",
    "HttpGitHubAuthorizationApi",
    "UnavailableGitHubOAuthGrantStore",
    "compose_github_authorization_provisioner_from_env",
]
