"""Shared security boundary for Hub-owned Git source connectors.

The connector boundary only accepts server-side connection references. Remote
URLs and opaque credential references are resolved inside the Hub, validated
by the common Git remote policy, and are never included in worker-facing
content requests or connector results.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, TypeVar

from agent.services.git_remote_policy_service import (
    GitTransportAuthorization,
    GitRemoteAccessPolicyPort,
    GitRemotePolicyError,
    GitRemotePolicyRequest,
)
from agent.sources.source_connectors import (
    ConnectorHealth,
    ConnectorRefreshRequest,
    SourceConnector,
    SourceConnectorError,
    SourceInventory,
    SourceRevisionResolution,
)


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_COMMIT_SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_DESCRIPTOR_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "clone_url",
        "credential",
        "credential_ref",
        "credential_url",
        "credentials",
        "oauth_token",
        "password",
        "private_key",
        "remote_url",
        "secret",
        "token",
        "url",
    }
)
_AUTHORIZATION_REASONS = frozenset(
    {
        "authorization_expired",
        "authorization_missing",
        "authorization_required",
        "authorization_revoked",
        "credential_revoked",
        "github_installation_revoked",
        "github_scope_loss",
        "revoked",
        "scope_loss",
    }
)


class GitConnectorProviderError(RuntimeError):
    """Content-free provider error safe to translate across the connector port."""

    def __init__(self, reason_code: str) -> None:
        clean_reason = str(reason_code or "").strip().lower()
        super().__init__(clean_reason or "git_provider_failed")
        self.reason_code = clean_reason or "git_provider_failed"


@dataclass(frozen=True)
class GitSourceScope:
    tenant_id: str
    project_id: str
    owner_id: str

    @classmethod
    def from_descriptor(cls, descriptor: Mapping[str, Any]) -> "GitSourceScope":
        values = {
            name: str(descriptor.get(name) or "").strip()
            for name in ("tenant_id", "project_id", "owner_id")
        }
        if any(_IDENTIFIER.fullmatch(value) is None for value in values.values()):
            raise SourceConnectorError("source_scope_invalid")
        return cls(**values)


@dataclass(frozen=True)
class GitRepositoryBudgets:
    """Hard limits passed to providers and checked again at the Hub boundary."""

    max_submodules: int = 0
    max_lfs_objects: int = 0
    max_lfs_bytes: int = 0
    max_objects: int = 100_000
    max_pack_bytes: int = 256 * 1024 * 1024
    max_files: int = 100_000
    max_file_bytes: int = 16 * 1024 * 1024
    max_total_file_bytes: int = 512 * 1024 * 1024
    max_elapsed_seconds: float = 120.0
    max_egress_bytes: int = 512 * 1024 * 1024

    def __post_init__(self) -> None:
        integer_fields = (
            "max_submodules",
            "max_lfs_objects",
            "max_lfs_bytes",
            "max_objects",
            "max_pack_bytes",
            "max_files",
            "max_file_bytes",
            "max_total_file_bytes",
            "max_egress_bytes",
        )
        for name in integer_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("git_connector_budget_invalid")
        if (
            isinstance(self.max_elapsed_seconds, bool)
            or not isinstance(self.max_elapsed_seconds, (int, float))
            or not math.isfinite(float(self.max_elapsed_seconds))
            or float(self.max_elapsed_seconds) <= 0
        ):
            raise ValueError("git_connector_budget_invalid")


@dataclass(frozen=True)
class GitRemoteEndpoint:
    """Server-side endpoint projection; never delegated to content providers."""

    connection_ref: str
    remote_url: str
    credential_ref: str | None
    authorization_state: str
    granted_scopes: frozenset[str]


@dataclass(frozen=True)
class GitCommitResolution:
    requested_ref: str
    commit_sha: str


@dataclass(frozen=True)
class GitContentRequest:
    """Secret-free request sent to inventory and refresh provider ports."""

    scope: GitSourceScope
    connector_type: str
    connection_ref: str
    repository_identifier: str | None
    requested_ref: str
    commit_sha: str
    budgets: GitRepositoryBudgets
    transport_authorization: GitTransportAuthorization
    source_id: str = ""
    source_revision_digest: str = ""
    recurse_submodules: bool = False
    lfs_mode: str = "disabled"
    follow_redirects: bool = False
    proxy_url: str | None = None


@dataclass(frozen=True)
class GitRepositoryMetrics:
    item_count: int
    object_count: int
    pack_bytes: int
    file_count: int
    largest_file_bytes: int
    total_file_bytes: int
    submodule_count: int
    lfs_object_count: int
    lfs_bytes: int
    elapsed_seconds: float
    egress_bytes: int
    manifest_digest: str
    exclusions: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class GitStoredPayloadQuery:
    scope: GitSourceScope
    connector_type: str
    source_id: str
    connection_ref: str
    repository_identifier: str | None
    requested_ref: str


@dataclass(frozen=True)
class GitMaterializedFile:
    relative_path: str
    mode: str
    content_digest: str
    byte_size: int
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class GitRepositoryMaterialization:
    metrics: GitRepositoryMetrics
    files: tuple[GitMaterializedFile, ...]


class GitInventoryProviderPort(Protocol):
    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool: ...

    def inventory(self, request: GitContentRequest) -> GitRepositoryMetrics: ...


class GitRefreshProviderPort(Protocol):
    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool: ...

    def fetch(self, request: GitContentRequest) -> GitRepositoryMetrics: ...


_T = TypeVar("_T")


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def git_source_revision_digest(
    *, connector_type: str, source_id: str, commit_sha: str
) -> str:
    return _canonical_digest(
        {
            "commit_sha": str(commit_sha).lower(),
            "connector_type": str(connector_type),
            "source_id": str(source_id),
        }
    )


def _contains_forbidden_descriptor_value(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key or "").strip().lower()
            if (
                normalized in _FORBIDDEN_DESCRIPTOR_KEYS
                or normalized.endswith("_token")
                or normalized.endswith("_password")
                or normalized.endswith("_secret")
                or normalized.endswith("_url")
            ):
                return True
            if _contains_forbidden_descriptor_value(child):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_descriptor_value(item) for item in value)
    return False


def validate_requested_ref(value: str) -> bool:
    ref = str(value or "").strip()
    if not ref or len(ref) > 255:
        return False
    if ref.startswith(("-", ".")) or ref.endswith(("/", ".", ".lock")):
        return False
    if any(ord(character) < 32 or ord(character) == 127 for character in ref):
        return False
    return not any(
        forbidden in ref
        for forbidden in ("..", "@{", "\\", "//", "~", "^", ":", "?", "*", "[")
    )


def _is_authorization_reason(reason_code: str) -> bool:
    normalized = str(reason_code or "").strip().lower()
    return (
        normalized in _AUTHORIZATION_REASONS
        or "revoked" in normalized
        or "scope_loss" in normalized
        or normalized.startswith("authorization_")
        or (
            "credential" in normalized
            and any(
                token in normalized
                for token in ("inactive", "missing", "required", "status")
            )
        )
    )


class GitSourceConnectorBase(ABC):
    """Template adapter keeping policy and budget enforcement provider-neutral."""

    connector_type: str
    required_scopes: frozenset[str]

    def __init__(
        self,
        *,
        remote_policy: GitRemoteAccessPolicyPort,
        inventory_provider: GitInventoryProviderPort,
        refresh_provider: GitRefreshProviderPort,
        budgets: GitRepositoryBudgets | None = None,
    ) -> None:
        self._remote_policy = remote_policy
        self._inventory_provider = inventory_provider
        self._refresh_provider = refresh_provider
        self._budgets = budgets or GitRepositoryBudgets()

    def validate(self, descriptor: Mapping[str, Any]) -> tuple[str, ...]:
        errors: list[str] = []
        source_id = str(descriptor.get("source_id") or "").strip()
        source_type = str(descriptor.get("source_type") or "").strip().lower()
        if _IDENTIFIER.fullmatch(source_id) is None:
            errors.append("source_id_invalid")
        if source_type != self.connector_type:
            errors.append("connector_type_mismatch")
        try:
            GitSourceScope.from_descriptor(descriptor)
        except SourceConnectorError:
            errors.append("source_scope_invalid")
        if _contains_forbidden_descriptor_value(descriptor):
            errors.append("raw_credentials_forbidden")
        errors.extend(self._connector_validation_errors(descriptor))
        return tuple(dict.fromkeys(errors))

    def resolve_revision(
        self,
        descriptor: Mapping[str, Any],
    ) -> SourceRevisionResolution:
        content_request = self._resolve_content_request(
            descriptor, prefer_stored=True
        )
        return self._revision_projection(descriptor, content_request)

    def assert_indexable(
        self,
        descriptor: Mapping[str, Any],
    ) -> SourceRevisionResolution:
        """Admission seam: authorization failure prevents an index revision."""

        return self.resolve_revision(descriptor)

    def inventory(self, descriptor: Mapping[str, Any]) -> SourceInventory:
        content_request = self._resolve_content_request(
            descriptor, prefer_stored=True
        )
        self._require_transport_support(
            self._inventory_provider,
            content_request.transport_authorization,
        )
        metrics = self._invoke_provider(
            lambda: self._inventory_provider.inventory(content_request)
        )
        self._enforce_metrics(metrics)
        return SourceInventory(
            item_count=metrics.item_count,
            total_bytes=metrics.total_file_bytes,
            exclusions=tuple(metrics.exclusions),
            manifest_digest=metrics.manifest_digest,
        )

    def refresh(
        self,
        descriptor: Mapping[str, Any],
        request: ConnectorRefreshRequest,
    ) -> Mapping[str, Any]:
        content_request = self._resolve_content_request(descriptor)
        revision = self._revision_projection(descriptor, content_request)
        source_id = str(descriptor.get("source_id") or "").strip()
        if request.dry_run:
            return {
                "source_id": source_id,
                "status": "planned",
                "reason_code": "dry_run",
                "immutable_ref": revision.immutable_ref,
                "revision_digest": revision.revision_digest,
            }
        self._require_transport_support(
            self._refresh_provider,
            content_request.transport_authorization,
        )
        metrics = self._invoke_provider(
            lambda: self._refresh_provider.fetch(content_request)
        )
        self._enforce_metrics(metrics)
        return {
            "source_id": source_id,
            "status": "ok",
            "immutable_ref": revision.immutable_ref,
            "revision_digest": revision.revision_digest,
            "manifest_digest": metrics.manifest_digest,
            "item_count": metrics.item_count,
            "total_bytes": metrics.total_file_bytes,
            "exclusions": list(metrics.exclusions),
        }

    def health(self, descriptor: Mapping[str, Any]) -> ConnectorHealth:
        errors = self.validate(descriptor)
        if errors:
            return ConnectorHealth(status="degraded", reason_code=errors[0])
        try:
            self._resolve_content_request(descriptor)
        except SourceConnectorError as exc:
            if exc.reason_code == "authorization_required":
                return ConnectorHealth(
                    status="authorization_required",
                    reason_code=exc.reason_code,
                )
            return ConnectorHealth(status="degraded", reason_code=exc.reason_code)
        return ConnectorHealth(status="healthy")

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_type=self.connector_type,
            validator=self,
            revision_resolver=self,
            inventory_provider=self,
            refresher=self,
            health_provider=self,
        )

    def _resolve_content_request(
        self,
        descriptor: Mapping[str, Any],
        *,
        prefer_stored: bool = False,
    ) -> GitContentRequest:
        errors = self.validate(descriptor)
        if errors:
            raise SourceConnectorError(errors[0])
        scope = GitSourceScope.from_descriptor(descriptor)
        connection_ref = self._connection_ref(descriptor)
        endpoint = self._invoke_provider(
            lambda: self._resolve_endpoint(scope, descriptor)
        )
        if endpoint.connection_ref != connection_ref:
            raise SourceConnectorError("provider_response_invalid")
        self._require_authorization(endpoint)
        transport_authorization = self._authorize_remote(
            endpoint,
            operation="fetch",
        )
        requested_ref = self._requested_ref(descriptor)
        source_id = str(descriptor.get("source_id") or "").strip()
        stored_resolver = getattr(
            self._inventory_provider, "resolve_stored_commit", None
        )
        if prefer_stored and callable(stored_resolver):
            stored_commit = self._invoke_provider(
                lambda: stored_resolver(
                    GitStoredPayloadQuery(
                        scope=scope,
                        connector_type=self.connector_type,
                        source_id=source_id,
                        connection_ref=connection_ref,
                        repository_identifier=self._repository_identifier(
                            descriptor
                        ),
                        requested_ref=requested_ref,
                    )
                )
            )
            commit = GitCommitResolution(
                requested_ref=requested_ref,
                commit_sha=str(stored_commit),
            )
        else:
            commit = self._invoke_provider(
                lambda: self._resolve_commit(
                    scope,
                    descriptor,
                    transport_authorization,
                )
            )
        if commit.requested_ref != requested_ref:
            raise SourceConnectorError("provider_response_invalid")
        commit_sha = str(commit.commit_sha or "").strip().lower()
        if _COMMIT_SHA.fullmatch(commit_sha) is None:
            raise SourceConnectorError("immutable_commit_invalid")
        return GitContentRequest(
            scope=scope,
            connector_type=self.connector_type,
            connection_ref=connection_ref,
            repository_identifier=self._repository_identifier(descriptor),
            requested_ref=requested_ref,
            commit_sha=commit_sha,
            budgets=self._budgets,
            transport_authorization=transport_authorization,
            source_id=source_id,
            source_revision_digest=git_source_revision_digest(
                connector_type=self.connector_type,
                source_id=source_id,
                commit_sha=commit_sha,
            ),
        )

    def _require_authorization(self, endpoint: GitRemoteEndpoint) -> None:
        state = str(endpoint.authorization_state or "").strip().lower()
        if state != "active":
            raise SourceConnectorError("authorization_required")
        scopes = frozenset(
            str(scope or "").strip().lower()
            for scope in endpoint.granted_scopes
            if str(scope or "").strip()
        )
        if not self.required_scopes.issubset(scopes):
            raise SourceConnectorError("authorization_required")

    def _authorize_remote(
        self,
        endpoint: GitRemoteEndpoint,
        *,
        operation: str,
    ) -> GitTransportAuthorization:
        request = GitRemotePolicyRequest(
            remote_url=endpoint.remote_url,
            operation=operation,
            credential_ref=endpoint.credential_ref,
            allow_redirects=False,
            proxy_url=None,
            recurse_submodules=False,
            lfs_mode="disabled",
        )
        try:
            authorized = self._remote_policy.authorize(request)
        except GitRemotePolicyError as exc:
            if _is_authorization_reason(exc.reason_code):
                raise SourceConnectorError("authorization_required") from None
            raise SourceConnectorError(exc.reason_code) from None
        if not hasattr(authorized, "canonical_url"):
            raise SourceConnectorError(
                "git_transport_authorization_missing"
            )
        plan = GitTransportAuthorization.create(
            authorized=authorized,
            request=request,
        )
        try:
            plan.validate()
        except GitRemotePolicyError as exc:
            raise SourceConnectorError(exc.reason_code) from None
        return plan

    @staticmethod
    def _require_transport_support(
        provider: Any,
        authorization: GitTransportAuthorization,
    ) -> None:
        """Fail closed unless the concrete transport enforces the pinned plan."""

        try:
            authorization.validate()
        except GitRemotePolicyError as exc:
            raise SourceConnectorError(exc.reason_code) from None
        supports = getattr(
            provider,
            "supports_transport_authorization",
            None,
        )
        if not callable(supports) or supports(authorization) is not True:
            raise SourceConnectorError(
                "git_transport_ip_pinning_unsupported"
            )

    def _revision_projection(
        self,
        descriptor: Mapping[str, Any],
        request: GitContentRequest,
    ) -> SourceRevisionResolution:
        digest = request.source_revision_digest or git_source_revision_digest(
            connector_type=self.connector_type,
            source_id=str(descriptor.get("source_id") or "").strip(),
            commit_sha=request.commit_sha,
        )
        metadata: dict[str, Any] = {
            "connector_type": self.connector_type,
            "commit_sha": request.commit_sha,
            "requested_ref": request.requested_ref,
        }
        metadata.update(self._public_metadata(descriptor))
        return SourceRevisionResolution(
            revision_digest=digest,
            immutable_ref=f"git-commit:{request.commit_sha}",
            metadata=metadata,
        )

    def _enforce_metrics(self, metrics: GitRepositoryMetrics) -> None:
        integer_fields = (
            "item_count",
            "object_count",
            "pack_bytes",
            "file_count",
            "largest_file_bytes",
            "total_file_bytes",
            "submodule_count",
            "lfs_object_count",
            "lfs_bytes",
            "egress_bytes",
        )
        if any(
            isinstance(getattr(metrics, name), bool)
            or not isinstance(getattr(metrics, name), int)
            or getattr(metrics, name) < 0
            for name in integer_fields
        ):
            raise SourceConnectorError("provider_response_invalid")
        if (
            isinstance(metrics.elapsed_seconds, bool)
            or not isinstance(metrics.elapsed_seconds, (int, float))
            or not math.isfinite(float(metrics.elapsed_seconds))
            or float(metrics.elapsed_seconds) < 0
            or _DIGEST.fullmatch(str(metrics.manifest_digest or "").strip())
            is None
            or any(not isinstance(item, Mapping) for item in metrics.exclusions)
        ):
            raise SourceConnectorError("provider_response_invalid")

        checks = (
            (
                metrics.submodule_count,
                self._budgets.max_submodules,
                "git_submodule_budget_exceeded",
            ),
            (
                metrics.lfs_object_count,
                self._budgets.max_lfs_objects,
                "git_lfs_budget_exceeded",
            ),
            (
                metrics.lfs_bytes,
                self._budgets.max_lfs_bytes,
                "git_lfs_budget_exceeded",
            ),
            (
                metrics.object_count,
                self._budgets.max_objects,
                "git_object_budget_exceeded",
            ),
            (
                metrics.pack_bytes,
                self._budgets.max_pack_bytes,
                "git_pack_budget_exceeded",
            ),
            (
                metrics.file_count,
                self._budgets.max_files,
                "git_file_count_budget_exceeded",
            ),
            (
                metrics.largest_file_bytes,
                self._budgets.max_file_bytes,
                "git_file_budget_exceeded",
            ),
            (
                metrics.total_file_bytes,
                self._budgets.max_total_file_bytes,
                "git_total_file_budget_exceeded",
            ),
            (
                float(metrics.elapsed_seconds),
                float(self._budgets.max_elapsed_seconds),
                "git_time_budget_exceeded",
            ),
            (
                metrics.egress_bytes,
                self._budgets.max_egress_bytes,
                "git_egress_budget_exceeded",
            ),
        )
        for actual, limit, reason_code in checks:
            if actual > limit:
                raise SourceConnectorError(reason_code)

    @staticmethod
    def _invoke_provider(operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except GitConnectorProviderError as exc:
            if _is_authorization_reason(exc.reason_code):
                raise SourceConnectorError("authorization_required") from None
            raise SourceConnectorError(exc.reason_code) from None
        except SourceConnectorError:
            raise
        except Exception:
            raise SourceConnectorError("git_provider_unavailable") from None

    @abstractmethod
    def _connector_validation_errors(
        self,
        descriptor: Mapping[str, Any],
    ) -> tuple[str, ...]: ...

    @abstractmethod
    def _connection_ref(self, descriptor: Mapping[str, Any]) -> str: ...

    @abstractmethod
    def _repository_identifier(
        self,
        descriptor: Mapping[str, Any],
    ) -> str | None: ...

    @abstractmethod
    def _requested_ref(self, descriptor: Mapping[str, Any]) -> str: ...

    @abstractmethod
    def _resolve_endpoint(
        self,
        scope: GitSourceScope,
        descriptor: Mapping[str, Any],
    ) -> GitRemoteEndpoint: ...

    @abstractmethod
    def _resolve_commit(
        self,
        scope: GitSourceScope,
        descriptor: Mapping[str, Any],
        transport_authorization: GitTransportAuthorization,
    ) -> GitCommitResolution: ...

    @abstractmethod
    def _public_metadata(
        self,
        descriptor: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


__all__ = [
    "GitCommitResolution",
    "GitConnectorProviderError",
    "GitContentRequest",
    "GitInventoryProviderPort",
    "GitRefreshProviderPort",
    "GitRemoteEndpoint",
    "GitRepositoryBudgets",
    "GitRepositoryMetrics",
    "GitSourceConnectorBase",
    "GitSourceScope",
    "validate_requested_ref",
]
