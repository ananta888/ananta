"""Concrete Hub providers behind GitHubRepository and GenericGit ports."""

from __future__ import annotations

from agent.services.git_remote_policy_service import GitTransportAuthorization
from agent.services.hub_git_authorization_registry import (
    HubGitAuthorizationRegistryPort,
    RegisteredGitAuthorization,
)
from agent.services.hub_git_transport import HubGitTransportPort
from agent.services.remote_source_payload_store import (
    RemoteSourcePayloadStorePort,
    authorization_binding_digest,
)
from agent.sources.generic_git_connector import (
    GenericGitCommitResolutionRequest,
    RegisteredGitRemoteRequest,
)
from agent.sources.git_source_connector_common import (
    GitCommitResolution,
    GitConnectorProviderError,
    GitContentRequest,
    GitRemoteEndpoint,
    GitRepositoryMetrics,
    GitStoredPayloadQuery,
)
from agent.sources.github_repository_connector import (
    GitHubCommitResolutionRequest,
    GitHubRepositoryEndpointRequest,
)
from agent.sources.source_connectors import SourceConnectorError


def _endpoint(record: RegisteredGitAuthorization) -> GitRemoteEndpoint:
    return GitRemoteEndpoint(
        connection_ref=record.connection_ref,
        remote_url=record.remote_url,
        credential_ref=record.credential_ref,
        authorization_state=record.authorization_state,
        granted_scopes=record.granted_scopes,
    )


def _require_active(
    record: RegisteredGitAuthorization | None,
    *,
    required_scope: str,
) -> RegisteredGitAuthorization:
    if (
        record is None
        or record.authorization_state != "active"
        or required_scope not in record.granted_scopes
    ):
        raise SourceConnectorError("authorization_required")
    return record


def _require_plan_binding(
    record: RegisteredGitAuthorization,
    authorization: GitTransportAuthorization,
) -> None:
    try:
        authorization.validate()
    except Exception:
        raise SourceConnectorError(
            "git_transport_authorization_invalid"
        ) from None
    if (
        authorization.canonical_url != record.remote_url
        or authorization.credential_ref != record.credential_ref
    ):
        raise SourceConnectorError(
            "git_transport_authorization_invalid"
        )


class HubGitHubRepositoryEndpointProvider:
    def __init__(
        self,
        *,
        registry: HubGitAuthorizationRegistryPort,
    ) -> None:
        self._registry = registry

    def resolve_repository(
        self,
        request: GitHubRepositoryEndpointRequest,
    ) -> GitRemoteEndpoint:
        record = self._registry.resolve_github(
            scope=request.scope,
            authorization_ref=request.authorization_ref,
            repository=request.repository,
        )
        if record is None:
            raise SourceConnectorError("authorization_required")
        return _endpoint(record)


class HubRegisteredGitRemoteProvider:
    def __init__(
        self,
        *,
        registry: HubGitAuthorizationRegistryPort,
    ) -> None:
        self._registry = registry

    def get_registered_remote(
        self,
        request: RegisteredGitRemoteRequest,
    ) -> GitRemoteEndpoint | None:
        record = self._registry.resolve_generic(
            scope=request.scope,
            remote_id=request.remote_id,
        )
        return _endpoint(record) if record is not None else None


class HubGitHubCommitResolver:
    def __init__(
        self,
        *,
        registry: HubGitAuthorizationRegistryPort,
        transport: HubGitTransportPort,
    ) -> None:
        self._registry = registry
        self._transport = transport

    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool:
        return self._transport.supports_authorization(authorization)

    def resolve_commit(
        self,
        request: GitHubCommitResolutionRequest,
    ) -> GitCommitResolution:
        record = _require_active(
            self._registry.resolve_github(
                scope=request.scope,
                authorization_ref=request.authorization_ref,
                repository=request.repository,
            ),
            required_scope="contents:read",
        )
        _require_plan_binding(record, request.transport_authorization)
        try:
            commit = self._transport.resolve_commit(
                authorization=request.transport_authorization,
                credential_username=record.credential_username,
                requested_ref=request.requested_ref,
            )
        except GitConnectorProviderError as exc:
            raise SourceConnectorError(exc.reason_code) from None
        return GitCommitResolution(
            requested_ref=request.requested_ref,
            commit_sha=commit,
        )


class HubGenericGitCommitResolver:
    def __init__(
        self,
        *,
        registry: HubGitAuthorizationRegistryPort,
        transport: HubGitTransportPort,
    ) -> None:
        self._registry = registry
        self._transport = transport

    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool:
        return self._transport.supports_authorization(authorization)

    def resolve_commit(
        self,
        request: GenericGitCommitResolutionRequest,
    ) -> GitCommitResolution:
        record = _require_active(
            self._registry.resolve_generic(
                scope=request.scope,
                remote_id=request.remote_id,
            ),
            required_scope="repository:read",
        )
        _require_plan_binding(record, request.transport_authorization)
        try:
            commit = self._transport.resolve_commit(
                authorization=request.transport_authorization,
                credential_username=record.credential_username,
                requested_ref=request.requested_ref,
            )
        except GitConnectorProviderError as exc:
            raise SourceConnectorError(exc.reason_code) from None
        return GitCommitResolution(
            requested_ref=request.requested_ref,
            commit_sha=commit,
        )


class HubGitContentProvider:
    def __init__(
        self,
        *,
        registry: HubGitAuthorizationRegistryPort,
        transport: HubGitTransportPort,
        payload_store: RemoteSourcePayloadStorePort | None = None,
    ) -> None:
        self._registry = registry
        self._transport = transport
        self._payloads = payload_store

    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool:
        return self._transport.supports_authorization(authorization)

    def inventory(self, request: GitContentRequest) -> GitRepositoryMetrics:
        record = self._record_for(request)
        if self._payloads is not None:
            try:
                return self._payloads.inventory(
                    request=request,
                    authorization_binding_digest=authorization_binding_digest(
                        record
                    ),
                )
            except GitConnectorProviderError as exc:
                raise SourceConnectorError(exc.reason_code) from None
        return self._transport.inspect_content(
            request,
            credential_username=record.credential_username,
        )

    def fetch(self, request: GitContentRequest) -> GitRepositoryMetrics:
        record = self._record_for(request)
        if self._payloads is not None:
            try:
                materialized = self._transport.materialize_content(
                    request,
                    credential_username=record.credential_username,
                )
                self._payloads.persist(
                    request=request,
                    materialization=materialized,
                    authorization_binding_digest=authorization_binding_digest(
                        record
                    ),
                )
                return materialized.metrics
            except GitConnectorProviderError as exc:
                raise SourceConnectorError(exc.reason_code) from None
        return self._transport.inspect_content(
            request,
            credential_username=record.credential_username,
        )

    def resolve_stored_commit(self, query: GitStoredPayloadQuery) -> str:
        if self._payloads is None:
            raise GitConnectorProviderError("remote_source_payload_required")
        request = type(
            "StoredPayloadAuthorizationRequest",
            (),
            {
                "scope": query.scope,
                "connection_ref": query.connection_ref,
                "repository_identifier": query.repository_identifier,
            },
        )()
        record = self._record_for(request)
        return self._payloads.resolve_stored_commit(
            query=query,
            authorization_binding_digest=authorization_binding_digest(record),
        )

    def _record_for(
        self,
        request: GitContentRequest,
    ) -> RegisteredGitAuthorization:
        record = self._registry.resolve_connection(
            scope=request.scope,
            connection_ref=request.connection_ref,
            repository_identifier=request.repository_identifier,
        )
        required_scope = (
            "contents:read"
            if record is not None
            and record.authorization_kind.startswith("github_")
            else "repository:read"
        )
        active = _require_active(record, required_scope=required_scope)
        _require_plan_binding(active, request.transport_authorization)
        return active


__all__ = [
    "HubGenericGitCommitResolver",
    "HubGitContentProvider",
    "HubGitHubCommitResolver",
    "HubGitHubRepositoryEndpointProvider",
    "HubRegisteredGitRemoteProvider",
]
