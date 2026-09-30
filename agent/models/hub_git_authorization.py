"""Registered Hub Git authorization value type (dependency-free contract).

Repositories and services share this record; it carries no service logic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agent.sources.git_source_connector_common import GitSourceScope

_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_KINDS = frozenset(
    {"github_app", "github_oauth", "github_public", "generic_git"}
)
_STATES = frozenset({"active", "revoked", "scope_loss"})


class HubGitAuthorizationRegistryError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


@dataclass(frozen=True, repr=False)
class RegisteredGitAuthorization:
    scope: GitSourceScope
    connection_ref: str
    authorization_kind: str
    remote_url: str
    credential_ref: str | None
    credential_username: str | None
    authorization_state: str
    granted_scopes: frozenset[str]
    repository: str | None = None

    def __post_init__(self) -> None:
        connection_ref = str(self.connection_ref or "").strip()
        authorization_kind = str(self.authorization_kind or "").strip()
        state = str(self.authorization_state or "").strip().lower()
        repository = (
            str(self.repository).strip() if self.repository is not None else None
        )
        if (
            _REFERENCE.fullmatch(connection_ref) is None
            or authorization_kind not in _KINDS
            or state not in _STATES
            or not str(self.remote_url or "").strip()
            or (
                authorization_kind.startswith("github_")
                and (
                    repository is None
                    or _REPOSITORY.fullmatch(repository) is None
                )
            )
            or (
                authorization_kind == "generic_git"
                and repository is not None
            )
        ):
            raise HubGitAuthorizationRegistryError(
                "git_authorization_registration_invalid"
            )
        object.__setattr__(self, "connection_ref", connection_ref)
        object.__setattr__(self, "authorization_kind", authorization_kind)
        object.__setattr__(self, "authorization_state", state)
        object.__setattr__(self, "repository", repository)
        object.__setattr__(
            self,
            "granted_scopes",
            frozenset(
                str(item or "").strip().lower()
                for item in self.granted_scopes
                if str(item or "").strip()
            ),
        )

    def __repr__(self) -> str:
        return (
            "RegisteredGitAuthorization("
            f"connection_ref={self.connection_ref!r}, "
            f"authorization_kind={self.authorization_kind!r}, "
            f"authorization_state={self.authorization_state!r}, "
            "remote_url=<redacted>, credential_ref=<opaque>)"
        )


class HubGitAuthorizationPersistenceError(RuntimeError):
    """Stable, content-free persistence failure."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


__all__ = [
    "HubGitAuthorizationPersistenceError",
    "HubGitAuthorizationRegistryError",
    "RegisteredGitAuthorization",
]
