from __future__ import annotations

from dataclasses import replace

import pytest

from agent.services.git_remote_policy_service import (
    AuthorizedGitRemote,
    GitRemotePolicyError,
    GitTransportAuthorization,
)
from agent.sources.generic_git_connector import GenericGitConnector
from agent.sources.git_source_connector_common import (
    GitCommitResolution,
    GitRemoteEndpoint,
    GitRepositoryBudgets,
    GitRepositoryMetrics,
)
from agent.sources.github_repository_connector import GitHubRepositoryConnector
from agent.sources.source_connectors import (
    ConnectorRefreshRequest,
    ConnectorRegistry,
    SourceConnectorError,
)


COMMIT = "a" * 40
MANIFEST = "b" * 64
SCOPE = {
    "tenant_id": "tenant-1",
    "project_id": "project-1",
    "owner_id": "owner-1",
}
GITHUB_DESCRIPTOR = {
    **SCOPE,
    "source_id": "source-github",
    "source_type": "github_repository",
    "github_authorization_ref": "github-installation:installation-1",
    "repository": "ananta/example",
    "ref": "main",
}
GENERIC_DESCRIPTOR = {
    **SCOPE,
    "source_id": "source-generic",
    "source_type": "generic_git",
    "remote_id": "remote-primary",
    "ref": "release",
}
METRICS = GitRepositoryMetrics(
    item_count=4,
    object_count=10,
    pack_bytes=1024,
    file_count=4,
    largest_file_bytes=256,
    total_file_bytes=512,
    submodule_count=0,
    lfs_object_count=0,
    lfs_bytes=0,
    elapsed_seconds=0.5,
    egress_bytes=2048,
    manifest_digest=MANIFEST,
)


class FakeRemotePolicy:
    def __init__(self, reason_code: str | None = None) -> None:
        self.reason_code = reason_code
        self.requests = []

    def authorize(self, request):
        self.requests.append(request)
        if self.reason_code:
            raise GitRemotePolicyError(self.reason_code)
        is_ssh = request.remote_url.startswith("ssh://")
        return AuthorizedGitRemote(
            canonical_url=request.remote_url,
            redacted_url=request.remote_url,
            scheme="ssh" if is_ssh else "https",
            host="code.example.test" if is_ssh else "github.com",
            port=22 if is_ssh else 443,
            resolved_ips=("93.184.216.34",),
            credential_ref=request.credential_ref,
        )


class FakeContentProvider:
    def __init__(
        self,
        metrics: GitRepositoryMetrics = METRICS,
        *,
        transport_supported: bool = True,
    ) -> None:
        self.metrics = metrics
        self.transport_supported = transport_supported
        self.inventory_requests = []
        self.fetch_requests = []

    def inventory(self, request):
        self.inventory_requests.append(request)
        return self.metrics

    def fetch(self, request):
        self.fetch_requests.append(request)
        return self.metrics

    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool:
        return self.transport_supported


class FakeGitHubEndpointProvider:
    def __init__(
        self,
        *,
        authorization_state: str = "active",
        scopes: frozenset[str] = frozenset({"contents:read"}),
    ) -> None:
        self.endpoint = GitRemoteEndpoint(
            connection_ref="github-installation:installation-1",
            remote_url="https://github.com/ananta/example.git",
            credential_ref="secret://github/installation-1",
            authorization_state=authorization_state,
            granted_scopes=scopes,
        )
        self.requests = []

    def resolve_repository(self, request):
        self.requests.append(request)
        return self.endpoint


class FakeGitHubCommitResolver:
    def __init__(self, *, transport_supported: bool = True) -> None:
        self.transport_supported = transport_supported
        self.requests = []

    def resolve_commit(self, request):
        self.requests.append(request)
        return GitCommitResolution(
            requested_ref=request.requested_ref,
            commit_sha=COMMIT,
        )

    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool:
        return self.transport_supported


class FakeRegisteredRemoteRegistry:
    def __init__(
        self,
        *,
        authorization_state: str = "active",
        scopes: frozenset[str] = frozenset({"repository:read"}),
    ) -> None:
        self.endpoint = GitRemoteEndpoint(
            connection_ref="remote-primary",
            remote_url="ssh://git@code.example.test/ananta/example.git",
            credential_ref="vault://git/remote-primary",
            authorization_state=authorization_state,
            granted_scopes=scopes,
        )
        self.requests = []

    def get_registered_remote(self, request):
        self.requests.append(request)
        return self.endpoint


class FakeGenericCommitResolver:
    def __init__(self) -> None:
        self.requests = []

    def resolve_commit(self, request):
        self.requests.append(request)
        return GitCommitResolution(
            requested_ref=request.requested_ref,
            commit_sha=COMMIT,
        )

    def supports_transport_authorization(
        self,
        authorization: GitTransportAuthorization,
    ) -> bool:
        return True


def github_connector(
    *,
    endpoint_provider=None,
    commit_resolver=None,
    remote_policy=None,
    content_provider=None,
    budgets=None,
):
    endpoint = endpoint_provider or FakeGitHubEndpointProvider()
    policy = remote_policy or FakeRemotePolicy()
    content = content_provider or FakeContentProvider()
    commit = commit_resolver or FakeGitHubCommitResolver()
    connector = GitHubRepositoryConnector(
        endpoint_provider=endpoint,
        commit_resolver=commit,
        remote_policy=policy,
        inventory_provider=content,
        refresh_provider=content,
        budgets=budgets,
    )
    return connector, endpoint, commit, policy, content


def generic_connector(
    *,
    remote_registry=None,
    remote_policy=None,
    content_provider=None,
):
    registry = remote_registry or FakeRegisteredRemoteRegistry()
    policy = remote_policy or FakeRemotePolicy()
    content = content_provider or FakeContentProvider()
    commit = FakeGenericCommitResolver()
    connector = GenericGitConnector(
        remote_registry=registry,
        commit_resolver=commit,
        remote_policy=policy,
        inventory_provider=content,
        refresh_provider=content,
    )
    return connector, registry, commit, policy, content


def test_both_git_adapters_are_additive_connector_registry_members() -> None:
    github, *_ = github_connector()
    generic, *_ = generic_connector()
    registry = ConnectorRegistry([github.connector(), generic.connector()])

    assert registry.list_types() == ("generic_git", "github_repository")


def test_github_resolves_ref_server_side_to_immutable_commit() -> None:
    connector, endpoint, commit, policy, _ = github_connector()

    revision = connector.resolve_revision(GITHUB_DESCRIPTOR)

    assert revision.immutable_ref == f"git-commit:{COMMIT}"
    assert revision.metadata["commit_sha"] == COMMIT
    assert "github_authorization_ref" not in revision.metadata
    assert endpoint.requests[0].authorization_ref == (
        "github-installation:installation-1"
    )
    assert commit.requests[0].requested_ref == "main"
    assert vars(commit.requests[0]).keys().isdisjoint(
        {"token", "credential_ref", "remote_url"}
    )
    assert policy.requests[0].remote_url.startswith("https://github.com/")


def test_github_rejects_raw_token_and_clone_url_before_provider_access() -> None:
    connector, endpoint, commit, _, _ = github_connector()
    descriptor = {
        **GITHUB_DESCRIPTOR,
        "token": "raw-token",
        "clone_url": "https://raw-token@github.com/ananta/example.git",
    }

    assert "raw_credentials_forbidden" in connector.validate(descriptor)
    with pytest.raises(SourceConnectorError) as exc:
        connector.resolve_revision(descriptor)

    assert exc.value.reason_code == "raw_credentials_forbidden"
    assert endpoint.requests == []
    assert commit.requests == []


@pytest.mark.parametrize("authorization_state", ("revoked", "scope_loss"))
def test_github_revocation_blocks_index_and_refresh(
    authorization_state: str,
) -> None:
    endpoint = FakeGitHubEndpointProvider(
        authorization_state=authorization_state
    )
    connector, _, commit, policy, content = github_connector(
        endpoint_provider=endpoint
    )

    with pytest.raises(SourceConnectorError) as index_error:
        connector.assert_indexable(GITHUB_DESCRIPTOR)
    with pytest.raises(SourceConnectorError) as refresh_error:
        connector.refresh(GITHUB_DESCRIPTOR, ConnectorRefreshRequest())

    assert index_error.value.reason_code == "authorization_required"
    assert refresh_error.value.reason_code == "authorization_required"
    assert commit.requests == []
    assert policy.requests == []
    assert content.fetch_requests == []


def test_github_scope_loss_blocks_before_ref_network_access() -> None:
    endpoint = FakeGitHubEndpointProvider(scopes=frozenset({"metadata:read"}))
    connector, _, commit, policy, _ = github_connector(
        endpoint_provider=endpoint
    )

    with pytest.raises(SourceConnectorError) as exc:
        connector.resolve_revision(GITHUB_DESCRIPTOR)

    assert exc.value.reason_code == "authorization_required"
    assert commit.requests == []
    assert policy.requests == []


@pytest.mark.parametrize(
    ("metrics", "reason_code"),
    (
        (
            replace(METRICS, submodule_count=1),
            "git_submodule_budget_exceeded",
        ),
        (
            replace(METRICS, lfs_object_count=1),
            "git_lfs_budget_exceeded",
        ),
        (
            replace(METRICS, object_count=100_001),
            "git_object_budget_exceeded",
        ),
        (
            replace(METRICS, pack_bytes=256 * 1024 * 1024 + 1),
            "git_pack_budget_exceeded",
        ),
        (
            replace(METRICS, largest_file_bytes=16 * 1024 * 1024 + 1),
            "git_file_budget_exceeded",
        ),
        (
            replace(METRICS, total_file_bytes=512 * 1024 * 1024 + 1),
            "git_total_file_budget_exceeded",
        ),
        (
            replace(METRICS, elapsed_seconds=120.1),
            "git_time_budget_exceeded",
        ),
        (
            replace(METRICS, egress_bytes=512 * 1024 * 1024 + 1),
            "git_egress_budget_exceeded",
        ),
    ),
)
def test_git_inventory_enforces_every_repository_budget(
    metrics: GitRepositoryMetrics,
    reason_code: str,
) -> None:
    content = FakeContentProvider(metrics)
    connector, *_ = github_connector(content_provider=content)

    with pytest.raises(SourceConnectorError) as exc:
        connector.inventory(GITHUB_DESCRIPTOR)

    assert exc.value.reason_code == reason_code


def test_budget_is_sent_to_provider_for_streaming_enforcement() -> None:
    budgets = GitRepositoryBudgets(max_objects=12, max_egress_bytes=4096)
    connector, *_, content = github_connector(budgets=budgets)

    connector.refresh(GITHUB_DESCRIPTOR, ConnectorRefreshRequest())

    assert content.fetch_requests[0].budgets is budgets
    assert content.fetch_requests[0].recurse_submodules is False
    assert content.fetch_requests[0].lfs_mode == "disabled"
    assert content.fetch_requests[0].follow_redirects is False
    assert content.fetch_requests[0].proxy_url is None


def test_generic_connector_uses_scoped_registered_remote_id_only() -> None:
    connector, registry, commit, _, content = generic_connector()

    result = connector.refresh(
        GENERIC_DESCRIPTOR,
        ConnectorRefreshRequest(),
    )

    assert result["immutable_ref"] == f"git-commit:{COMMIT}"
    assert registry.requests[0].remote_id == "remote-primary"
    assert registry.requests[0].scope.tenant_id == "tenant-1"
    assert commit.requests[0].remote_id == "remote-primary"
    assert vars(commit.requests[0]).keys().isdisjoint(
        {"token", "credential_ref", "remote_url"}
    )
    assert content.fetch_requests[0].connection_ref == "remote-primary"


def test_generic_connector_rejects_supplied_remote_url() -> None:
    connector, registry, commit, _, _ = generic_connector()
    descriptor = {
        **GENERIC_DESCRIPTOR,
        "remote_id": "https://example.test/repository.git",
        "remote_url": "https://example.test/repository.git",
    }

    assert "raw_credentials_forbidden" in connector.validate(descriptor)
    with pytest.raises(SourceConnectorError):
        connector.resolve_revision(descriptor)

    assert registry.requests == []
    assert commit.requests == []


@pytest.mark.parametrize("authorization_state", ("revoked", "scope_loss"))
def test_generic_revocation_blocks_index_and_refresh(
    authorization_state: str,
) -> None:
    registry = FakeRegisteredRemoteRegistry(
        authorization_state=authorization_state
    )
    connector, _, commit, _, content = generic_connector(
        remote_registry=registry
    )

    with pytest.raises(SourceConnectorError) as index_error:
        connector.assert_indexable(GENERIC_DESCRIPTOR)
    with pytest.raises(SourceConnectorError) as refresh_error:
        connector.refresh(GENERIC_DESCRIPTOR, ConnectorRefreshRequest())

    assert index_error.value.reason_code == "authorization_required"
    assert refresh_error.value.reason_code == "authorization_required"
    assert commit.requests == []
    assert content.fetch_requests == []


def test_remote_policy_denial_blocks_ref_resolution_and_fetch() -> None:
    policy = FakeRemotePolicy("git_remote_address_not_global")
    connector, _, commit, _, content = generic_connector(
        remote_policy=policy
    )

    with pytest.raises(SourceConnectorError) as exc:
        connector.refresh(GENERIC_DESCRIPTOR, ConnectorRefreshRequest())

    assert exc.value.reason_code == "git_remote_address_not_global"
    assert commit.requests == []
    assert content.fetch_requests == []


def test_transport_authorization_is_bound_to_resolution_and_fetch() -> None:
    connector, endpoint, commit, _, content = github_connector()

    connector.refresh(GITHUB_DESCRIPTOR, ConnectorRefreshRequest())

    authorization = content.fetch_requests[0].transport_authorization
    assert commit.requests[0].transport_authorization is authorization
    assert authorization.canonical_url == endpoint.endpoint.remote_url
    assert authorization.scheme == "https"
    assert authorization.host == "github.com"
    assert authorization.port == 443
    assert authorization.validated_ips == ("93.184.216.34",)
    assert authorization.redirects == "deny"
    assert authorization.proxy == "deny"
    assert authorization.recurse_submodules is False
    assert authorization.lfs_mode == "disabled"
    assert authorization.credential_ref == endpoint.endpoint.credential_ref
    assert endpoint.endpoint.credential_ref not in repr(authorization)


def test_fetch_provider_without_ip_pinning_fails_closed() -> None:
    content = FakeContentProvider(transport_supported=False)
    connector, _, _, _, _ = github_connector(content_provider=content)

    with pytest.raises(SourceConnectorError) as exc:
        connector.refresh(GITHUB_DESCRIPTOR, ConnectorRefreshRequest())

    assert exc.value.reason_code == "git_transport_ip_pinning_unsupported"
    assert content.fetch_requests == []


def test_commit_resolver_without_ip_pinning_fails_before_resolution() -> None:
    commit = FakeGitHubCommitResolver(transport_supported=False)
    connector, _, _, _, content = github_connector(commit_resolver=commit)

    with pytest.raises(SourceConnectorError) as exc:
        connector.resolve_revision(GITHUB_DESCRIPTOR)

    assert exc.value.reason_code == "git_transport_ip_pinning_unsupported"
    assert commit.requests == []
    assert content.fetch_requests == []
