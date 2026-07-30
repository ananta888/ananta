from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.services.git_remote_policy_service import (
    AuthorizedGitRemote,
    GitRemotePolicyRequest,
    GitTransportAuthorization,
)
from agent.services.hub_git_authorization_registry import (
    RegisteredGitAuthorization,
    ScopedGitAuthorizationRegistry,
)
from agent.services.hub_git_credential_resolver import GitCommandResult
from agent.services.hub_git_credential_resolver import (
    SubprocessGitCredentialCommandResolver,
)
from agent.services.hub_git_transport import HubGitTransport
from agent.sources.git_source_connector_common import (
    GitConnectorProviderError,
    GitRepositoryBudgets,
    GitSourceScope,
)
from agent.sources.hub_git_connector_providers import (
    HubGenericGitCommitResolver,
    HubGitContentProvider,
    HubGitHubRepositoryEndpointProvider,
    HubRegisteredGitRemoteProvider,
)
from agent.sources.generic_git_connector import (
    GenericGitCommitResolutionRequest,
    RegisteredGitRemoteRequest,
)
from agent.sources.source_connectors import SourceConnectorError
from agent.sources.github_repository_connector import (
    GitHubRepositoryEndpointRequest,
)


COMMIT = "a" * 40
SCOPE = GitSourceScope(
    tenant_id="tenant-1",
    project_id="project-1",
    owner_id="owner-1",
)


def _record(
    *,
    state: str = "active",
    scopes: frozenset[str] = frozenset({"contents:read"}),
) -> RegisteredGitAuthorization:
    return RegisteredGitAuthorization(
        scope=SCOPE,
        connection_ref="github-installation:installation-1",
        authorization_kind="github_app",
        repository="ananta/example",
        remote_url="https://github.com/ananta/example.git",
        credential_ref="secret://github/installation-1",
        credential_username="x-access-token",
        authorization_state=state,
        granted_scopes=scopes,
    )


def _authorization() -> GitTransportAuthorization:
    request = GitRemotePolicyRequest(
        remote_url="https://github.com/ananta/example.git",
        operation="fetch",
        credential_ref="secret://github/installation-1",
        lfs_mode="disabled",
    )
    return GitTransportAuthorization.create(
        authorized=AuthorizedGitRemote(
            canonical_url=request.remote_url,
            redacted_url=request.remote_url,
            scheme="https",
            host="github.com",
            port=443,
            resolved_ips=("93.184.216.34",),
            credential_ref=request.credential_ref,
        ),
        request=request,
    )


def _generic_record() -> RegisteredGitAuthorization:
    return RegisteredGitAuthorization(
        scope=SCOPE,
        connection_ref="remote-primary",
        authorization_kind="generic_git",
        remote_url="ssh://git@code.example.test/ananta/example.git",
        credential_ref="secret://git/remote-primary",
        credential_username=None,
        authorization_state="active",
        granted_scopes=frozenset({"repository:read"}),
    )


def _generic_authorization() -> GitTransportAuthorization:
    request = GitRemotePolicyRequest(
        remote_url="ssh://git@code.example.test/ananta/example.git",
        operation="fetch",
        credential_ref="secret://git/remote-primary",
        lfs_mode="disabled",
    )
    return GitTransportAuthorization.create(
        authorized=AuthorizedGitRemote(
            canonical_url=request.remote_url,
            redacted_url=request.remote_url,
            scheme="ssh",
            host="code.example.test",
            port=22,
            resolved_ips=("93.184.216.34",),
            credential_ref=request.credential_ref,
        ),
        request=request,
    )


class FakeCommandSession:
    def __init__(self, *, tree_size: int = 5) -> None:
        self.tree_size = tree_size
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        arguments,
        *,
        cwd: Path,
        timeout_seconds: float,
        maximum_file_bytes=None,
    ):
        del timeout_seconds, maximum_file_bytes
        command = tuple(str(item) for item in arguments)
        self.calls.append(command)
        if "ls-remote" in command:
            candidate = command[-1]
            return GitCommandResult(
                0,
                f"{COMMIT}\\t{candidate}\\n".encode("ascii"),
            )
        if "ls-tree" in command:
            return GitCommandResult(
                0,
                (
                    f"100644 blob {COMMIT} {self.tree_size}\\tREADME.md\\0"
                ).encode("ascii"),
            )
        if "count-objects" in command:
            return GitCommandResult(
                0,
                b"count: 1\\nsize: 0\\nin-pack: 0\\nsize-pack: 0\\n",
            )
        if "checkout" in command:
            (cwd / "README.md").write_bytes(b"hello")
        return GitCommandResult(0, b"")


class FakeCredentialResolver:
    def __init__(self, session: FakeCommandSession) -> None:
        self.session = session
        self.references: list[str | None] = []

    @contextmanager
    def open_session(
        self,
        *,
        credential_ref,
        credential_username,
        scheme,
    ):
        del credential_username, scheme
        self.references.append(credential_ref)
        yield self.session


def test_registry_is_tenant_project_owner_and_repository_scoped() -> None:
    registry = ScopedGitAuthorizationRegistry([_record()])
    provider = HubGitHubRepositoryEndpointProvider(registry=registry)

    endpoint = provider.resolve_repository(
        GitHubRepositoryEndpointRequest(
            scope=SCOPE,
            authorization_ref="github-installation:installation-1",
            repository="ananta/example",
        )
    )

    assert endpoint.authorization_state == "active"
    assert endpoint.credential_ref == "secret://github/installation-1"
    with pytest.raises(SourceConnectorError):
        provider.resolve_repository(
            GitHubRepositoryEndpointRequest(
                scope=GitSourceScope(
                    tenant_id="tenant-2",
                    project_id="project-1",
                    owner_id="owner-1",
                ),
                authorization_ref="github-installation:installation-1",
                repository="ananta/example",
            )
        )


@pytest.mark.parametrize(
    "record",
    (
        _record(state="revoked"),
        _record(state="scope_loss"),
        _record(scopes=frozenset({"metadata:read"})),
    ),
)
def test_revocation_and_scope_loss_are_projected_fail_closed(
    record: RegisteredGitAuthorization,
) -> None:
    endpoint = HubGitHubRepositoryEndpointProvider(
        registry=ScopedGitAuthorizationRegistry([record])
    ).resolve_repository(
        GitHubRepositoryEndpointRequest(
            scope=SCOPE,
            authorization_ref=record.connection_ref,
            repository="ananta/example",
        )
    )

    assert endpoint.authorization_state != "active" or (
        "contents:read" not in endpoint.granted_scopes
    )


def test_commit_resolution_uses_pinned_plan_and_opaque_reference(
    tmp_path: Path,
) -> None:
    session = FakeCommandSession()
    credentials = FakeCredentialResolver(session)
    transport = HubGitTransport(
        credential_resolver=credentials,
        workspace_root=tmp_path,
    )

    commit = transport.resolve_commit(
        authorization=_authorization(),
        credential_username="x-access-token",
        requested_ref="main",
    )

    assert commit == COMMIT
    assert credentials.references == ["secret://github/installation-1"]
    rendered = "\\n".join(item for call in session.calls for item in call)
    assert "http.curloptResolve=github.com:443:93.184.216.34" in rendered
    assert "secret://github/installation-1" not in rendered
    assert list(tmp_path.iterdir()) == []


def test_inventory_cleans_workspace_and_enforces_read_only_checkout(
    tmp_path: Path,
) -> None:
    session = FakeCommandSession()
    transport = HubGitTransport(
        credential_resolver=FakeCredentialResolver(session),
        workspace_root=tmp_path,
    )
    request = SimpleNamespace(
        transport_authorization=_authorization(),
        commit_sha=COMMIT,
        budgets=GitRepositoryBudgets(),
        scope=SCOPE,
        connection_ref="github-installation:installation-1",
    )

    metrics = transport.inspect_content(
        request,
        credential_username=None,
    )

    assert metrics.file_count == 1
    assert metrics.total_file_bytes == 5
    assert list(tmp_path.iterdir()) == []


def test_budget_failure_still_cleans_temporary_workspace(
    tmp_path: Path,
) -> None:
    session = FakeCommandSession(tree_size=128)
    transport = HubGitTransport(
        credential_resolver=FakeCredentialResolver(session),
        workspace_root=tmp_path,
    )
    request = SimpleNamespace(
        transport_authorization=_authorization(),
        commit_sha=COMMIT,
        budgets=GitRepositoryBudgets(max_total_file_bytes=16),
        scope=SCOPE,
        connection_ref="github-installation:installation-1",
    )

    with pytest.raises(
        GitConnectorProviderError,
        match="git_total_file_budget_exceeded",
    ):
        transport.inspect_content(request, credential_username=None)

    assert list(tmp_path.iterdir()) == []


def test_authorization_record_repr_redacts_remote_and_credential() -> None:
    rendered = repr(_record())

    assert "https://github.com/ananta/example.git" not in rendered
    assert "secret://github/installation-1" not in rendered


def test_content_provider_rechecks_revocation_before_fetch(
    tmp_path: Path,
) -> None:
    registry = ScopedGitAuthorizationRegistry([_record()])
    session = FakeCommandSession()
    transport = HubGitTransport(
        credential_resolver=FakeCredentialResolver(session),
        workspace_root=tmp_path,
    )
    provider = HubGitContentProvider(
        registry=registry,
        transport=transport,
    )
    registry.set_authorization_state(
        scope=SCOPE,
        connection_ref="github-installation:installation-1",
        repository="owner/repository",
        authorization_state="revoked",
    )
    request = SimpleNamespace(
        transport_authorization=_authorization(),
        commit_sha=COMMIT,
        budgets=GitRepositoryBudgets(),
        scope=SCOPE,
        connection_ref="github-installation:installation-1",
        repository_identifier="owner/repository",
    )

    with pytest.raises(SourceConnectorError, match="authorization_required"):
        provider.fetch(request)

    assert session.calls == []


def test_generic_provider_resolves_registered_remote_and_commit(
    tmp_path: Path,
) -> None:
    registry = ScopedGitAuthorizationRegistry([_generic_record()])
    remote_provider = HubRegisteredGitRemoteProvider(registry=registry)
    session = FakeCommandSession()
    transport = HubGitTransport(
        credential_resolver=FakeCredentialResolver(session),
        workspace_root=tmp_path,
    )
    resolver = HubGenericGitCommitResolver(
        registry=registry,
        transport=transport,
    )

    endpoint = remote_provider.get_registered_remote(
        RegisteredGitRemoteRequest(
            scope=SCOPE,
            remote_id="remote-primary",
        )
    )
    resolution = resolver.resolve_commit(
        GenericGitCommitResolutionRequest(
            scope=SCOPE,
            remote_id="remote-primary",
            requested_ref="main",
            transport_authorization=_generic_authorization(),
        )
    )

    assert endpoint is not None
    assert endpoint.connection_ref == "remote-primary"
    assert resolution.commit_sha == COMMIT
    assert "secret://git/remote-primary" not in "\n".join(
        item for call in session.calls for item in call
    )


class _FakeSecretResolver:
    def resolve(self, reference: str) -> str:
        assert reference == "secret://github/installation-1"
        return "TOP-SECRET-TOKEN"


def test_concrete_credential_session_keeps_secret_out_of_argv_and_repr(
    tmp_path: Path,
) -> None:
    resolver = SubprocessGitCredentialCommandResolver(
        secret_resolver=_FakeSecretResolver(),
        credential_root=tmp_path,
        base_environment={"PATH": "/usr/bin"},
    )
    completed = MagicMock(returncode=0, stdout=b"git version")

    with patch(
        "agent.services.hub_git_credential_resolver.subprocess.run",
        return_value=completed,
    ) as run:
        with resolver.open_session(
            credential_ref="secret://github/installation-1",
            credential_username="x-access-token",
            scheme="https",
        ) as session:
            result = session.run(["version"], cwd=tmp_path, timeout_seconds=1)
            rendered_session = repr(session)

    arguments = run.call_args.args[0]
    assert result.returncode == 0
    assert "TOP-SECRET-TOKEN" not in "\n".join(arguments)
    assert "TOP-SECRET-TOKEN" not in rendered_session
    assert "TOP-SECRET-TOKEN" not in repr(result)
    assert list(tmp_path.iterdir()) == []
