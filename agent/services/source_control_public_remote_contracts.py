"""Closed public contracts and immutable internal public-remote bindings."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass
from typing import Mapping

from agent.services.hub_git_authorization_registry import (
    RegisteredGitAuthorization,
)
from agent.sources.git_source_connector_common import GitSourceScope

_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,190}$")
_GITHUB_OWNER = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$"
)
_REPOSITORY_SEGMENT = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})$"
)
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,239}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_HANDLE = re.compile(r"^prv1_[A-Za-z0-9_-]{32,96}$")
_REMOTE_ID = re.compile(r"^pub_[A-Za-z0-9_-]{32,96}$")
_PROVIDERS = frozenset({"github_public", "https_git"})


class SourceControlPublicRemoteContractError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = str(reason_code)
        self.status_code = int(status_code)
        super().__init__(self.reason_code)


def _required_text(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SourceControlPublicRemoteContractError(
            "public_remote_payload_invalid"
        )
    return value.strip()


def _normalized_host(value: str) -> str:
    raw = value.strip().rstrip(".").lower()
    if not raw or ":" in raw or "/" in raw or "@" in raw:
        raise SourceControlPublicRemoteContractError(
            "public_remote_host_invalid"
        )
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        pass
    else:
        raise SourceControlPublicRemoteContractError(
            "public_remote_ip_literal_denied"
        )
    try:
        host = raw.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise SourceControlPublicRemoteContractError(
            "public_remote_host_invalid"
        ) from exc
    if (
        len(host) > 253
        or any(
            not label
            or len(label) > 63
            or label.startswith("-")
            or label.endswith("-")
            or re.fullmatch(r"[a-z0-9-]+", label) is None
            for label in host.split(".")
        )
    ):
        raise SourceControlPublicRemoteContractError(
            "public_remote_host_invalid"
        )
    return host


def _repository_path(value: str, *, github: bool) -> str:
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise SourceControlPublicRemoteContractError(
            "public_remote_repository_invalid"
        )
    segments = value.split("/")
    expected = 2 if github else None
    if (
        (expected is not None and len(segments) != expected)
        or not 1 <= len(segments) <= 16
        or len(value) > 512
        or any(
            segment in {".", ".."}
            or _REPOSITORY_SEGMENT.fullmatch(segment) is None
            for segment in segments
        )
    ):
        raise SourceControlPublicRemoteContractError(
            "public_remote_repository_invalid"
        )
    return "/".join(segments)


def _requested_ref(value: str) -> str:
    if (
        _REF.fullmatch(value) is None
        or ".." in value
        or "//" in value
        or "@{" in value
        or value.endswith(".lock")
    ):
        raise SourceControlPublicRemoteContractError(
            "public_remote_ref_invalid"
        )
    return value


@dataclass(frozen=True, repr=False)
class PublicRemoteSelection:
    provider: str
    host: str
    repository_path: str
    requested_ref: str

    def __post_init__(self) -> None:
        provider = str(self.provider or "").strip()
        if provider not in _PROVIDERS:
            raise SourceControlPublicRemoteContractError(
                "public_remote_provider_invalid"
            )
        host = _normalized_host(str(self.host or ""))
        repository = _repository_path(
            str(self.repository_path or "").strip(),
            github=provider == "github_public",
        )
        if provider == "github_public" and host != "github.com":
            raise SourceControlPublicRemoteContractError(
                "public_remote_github_host_invalid"
            )
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "host", host)
        object.__setattr__(self, "repository_path", repository)
        object.__setattr__(
            self,
            "requested_ref",
            _requested_ref(str(self.requested_ref or "").strip()),
        )

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
    ) -> "PublicRemoteSelection":
        provider = _required_text(payload, "provider")
        if provider == "github_public":
            if set(payload) != {
                "provider",
                "owner",
                "repository",
                "requested_ref",
            }:
                raise SourceControlPublicRemoteContractError(
                    "public_remote_payload_fields_invalid"
                )
            owner = _required_text(payload, "owner")
            repository = _required_text(payload, "repository")
            if _GITHUB_OWNER.fullmatch(owner) is None:
                raise SourceControlPublicRemoteContractError(
                    "public_remote_repository_invalid"
                )
            return cls(
                provider=provider,
                host="github.com",
                repository_path=f"{owner}/{repository}",
                requested_ref=_required_text(payload, "requested_ref"),
            )
        if provider == "https_git":
            if set(payload) != {
                "provider",
                "host",
                "repository",
                "requested_ref",
            }:
                raise SourceControlPublicRemoteContractError(
                    "public_remote_payload_fields_invalid"
                )
            return cls(
                provider=provider,
                host=_required_text(payload, "host"),
                repository_path=_required_text(payload, "repository"),
                requested_ref=_required_text(payload, "requested_ref"),
            )
        raise SourceControlPublicRemoteContractError(
            "public_remote_provider_invalid"
        )

    @property
    def internal_remote_url(self) -> str:
        return f"https://{self.host}/{self.repository_path}"

    @property
    def public_connector_type(self) -> str:
        return "github" if self.provider == "github_public" else "git"

    @property
    def authorization_kind(self) -> str:
        return (
            "github_public"
            if self.provider == "github_public"
            else "generic_git"
        )

    @property
    def granted_scopes(self) -> frozenset[str]:
        return frozenset(
            {"contents:read"}
            if self.provider == "github_public"
            else {"repository:read"}
        )

    def coordinates(self) -> Mapping[str, object]:
        return {
            "provider": self.provider,
            "host": self.host,
            "repository_path": self.repository_path,
            "requested_ref": self.requested_ref,
        }

    def __repr__(self) -> str:
        return (
            "PublicRemoteSelection("
            f"provider={self.provider!r}, endpoint=<redacted>)"
        )


@dataclass(frozen=True)
class PublicRemoteCreateSelection:
    validation_handle: str

    @classmethod
    def from_mapping(
        cls,
        payload: Mapping[str, object],
    ) -> "PublicRemoteCreateSelection":
        if set(payload) != {"validation_handle"}:
            raise SourceControlPublicRemoteContractError(
                "public_remote_create_fields_invalid"
            )
        handle = _required_text(payload, "validation_handle")
        if _HANDLE.fullmatch(handle) is None:
            raise SourceControlPublicRemoteContractError(
                "public_remote_validation_handle_invalid"
            )
        return cls(validation_handle=handle)

    @property
    def handle_digest(self) -> str:
        return hashlib.sha256(
            self.validation_handle.encode("ascii")
        ).hexdigest()


@dataclass(frozen=True, repr=False)
class PublicRemoteValidationBinding:
    scope: GitSourceScope
    selection: PublicRemoteSelection
    commit_sha: str
    policy_digest: str

    def __post_init__(self) -> None:
        if not all(
            _SCOPE_ID.fullmatch(str(value or ""))
            for value in (
                self.scope.tenant_id,
                self.scope.project_id,
                self.scope.owner_id,
            )
        ):
            raise SourceControlPublicRemoteContractError(
                "source_control_principal_scope_required",
                status_code=403,
            )
        commit = str(self.commit_sha or "").strip().lower()
        policy_digest = str(self.policy_digest or "").strip().lower()
        if _COMMIT.fullmatch(commit) is None:
            raise SourceControlPublicRemoteContractError(
                "public_remote_commit_invalid"
            )
        if _DIGEST.fullmatch(policy_digest) is None:
            raise SourceControlPublicRemoteContractError(
                "public_remote_policy_binding_invalid"
            )
        object.__setattr__(self, "commit_sha", commit)
        object.__setattr__(self, "policy_digest", policy_digest)

    @property
    def binding_digest(self) -> str:
        return _digest(
            {
                "scope": {
                    "tenant_id": self.scope.tenant_id,
                    "project_id": self.scope.project_id,
                    "owner_id": self.scope.owner_id,
                },
                "selection": self.selection.coordinates(),
                "commit_sha": self.commit_sha,
                "policy_digest": self.policy_digest,
            }
        )

    def __repr__(self) -> str:
        return (
            "PublicRemoteValidationBinding("
            f"provider={self.selection.provider!r}, "
            f"commit_sha={self.commit_sha!r}, endpoint=<redacted>)"
        )


@dataclass(frozen=True, repr=False)
class PublicRemoteRecord:
    remote_id: str
    binding: PublicRemoteValidationBinding
    created_at_epoch: float

    def __post_init__(self) -> None:
        if _REMOTE_ID.fullmatch(str(self.remote_id or "")) is None:
            raise SourceControlPublicRemoteContractError(
                "public_remote_id_invalid"
            )

    def registered_authorization(self) -> RegisteredGitAuthorization:
        selection = self.binding.selection
        return RegisteredGitAuthorization(
            scope=self.binding.scope,
            connection_ref=self.remote_id,
            authorization_kind=selection.authorization_kind,
            remote_url=selection.internal_remote_url,
            credential_ref=None,
            credential_username=None,
            authorization_state="active",
            granted_scopes=selection.granted_scopes,
            repository=(
                selection.repository_path
                if selection.provider == "github_public"
                else None
            ),
        )

    def __repr__(self) -> str:
        return (
            "PublicRemoteRecord("
            f"remote_id={self.remote_id!r}, endpoint=<redacted>)"
        )


def audit_binding_digest(
    *,
    scope: GitSourceScope,
    selection: PublicRemoteSelection,
) -> str:
    return _digest(
        {
            "scope": {
                "tenant_id": scope.tenant_id,
                "project_id": scope.project_id,
                "owner_id": scope.owner_id,
            },
            "selection": selection.coordinates(),
        }
    )


def _digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    ).hexdigest()


__all__ = [
    "PublicRemoteCreateSelection",
    "PublicRemoteRecord",
    "PublicRemoteSelection",
    "PublicRemoteValidationBinding",
    "SourceControlPublicRemoteContractError",
    "audit_binding_digest",
]
