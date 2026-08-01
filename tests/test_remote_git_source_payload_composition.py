from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, Session, create_engine

from agent.db_models.source_control import (
    SourceConnectionDB,
    SourceConnectionSelectorDB,
)
from agent.repositories.source_admission_receipt_repository import (
    SQLSourceAdmissionReceiptRepository,
)
from agent.repositories.source_control_repository import SQLSourceControlRepository
from agent.services.artifact_store import ArtifactStore
from agent.services.git_remote_policy_service import (
    AuthorizedGitRemote,
    GitRemotePolicyRequest,
    GitTransportAuthorization,
)
from agent.services.hub_git_authorization_registry import (
    RegisteredGitAuthorization,
    ScopedGitAuthorizationRegistry,
)
from agent.services.remote_git_bound_payload import (
    RemoteGitBoundSourcePayloadAdapter,
)
from agent.services.remote_git_source_admission import (
    RemoteGitSourceAdmissionService,
)
from agent.services.remote_source_payload_store import SQLRemoteSourcePayloadStore
from agent.services.source_admission_service import SourceAdmissionBudgets
from agent.services.source_control_index_authority_planner import (
    BoundSourceRevisionAuthority,
    BoundSourceRevisionPlanningError,
)
from agent.sources.git_source_connector_common import (
    GitContentRequest,
    GitMaterializedFile,
    GitRepositoryBudgets,
    GitRepositoryMaterialization,
    GitRepositoryMetrics,
    GitSourceScope,
    git_source_revision_digest,
)
from agent.sources.hub_git_connector_providers import HubGitContentProvider


COMMIT = "a" * 40
CONTENT = b"remote payload\n"
BLOB = __import__("hashlib").sha1(
    f"blob {len(CONTENT)}\0".encode("ascii") + CONTENT
).hexdigest()


class _Transport:
    def __init__(self) -> None:
        self.fetches = 0

    def supports_authorization(self, authorization):
        return True

    def materialize_content(self, request, *, credential_username):
        del request, credential_username
        self.fetches += 1
        return GitRepositoryMaterialization(
            metrics=GitRepositoryMetrics(
                item_count=1,
                object_count=1,
                pack_bytes=64,
                file_count=1,
                largest_file_bytes=len(CONTENT),
                total_file_bytes=len(CONTENT),
                submodule_count=0,
                lfs_object_count=0,
                lfs_bytes=0,
                elapsed_seconds=0.1,
                egress_bytes=64,
                manifest_digest="b" * 64,
            ),
            files=(
                GitMaterializedFile(
                    relative_path="README.md",
                    mode="100644",
                    content_digest=__import__("hashlib").sha256(CONTENT).hexdigest(),
                    byte_size=len(CONTENT),
                    content=CONTENT,
                ),
            ),
        )


def _authorization():
    request = GitRemotePolicyRequest(
        remote_url="https://github.com/ananta/example.git",
        operation="fetch",
        credential_ref="secret://github/example",
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


def test_git_fetch_scan_and_run_share_one_content_addressed_payload(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'state.db'}")
    SQLModel.metadata.create_all(engine)
    scope = GitSourceScope("tenant-1", "project-1", "owner-1")
    record = RegisteredGitAuthorization(
        scope=scope,
        connection_ref="github-installation:example",
        authorization_kind="github_app",
        repository="ananta/example",
        remote_url="https://github.com/ananta/example.git",
        credential_ref="secret://github/example",
        credential_username="x-access-token",
        authorization_state="active",
        granted_scopes=frozenset({"contents:read"}),
    )
    registry = ScopedGitAuthorizationRegistry([record])
    store = SQLRemoteSourcePayloadStore(
        session_factory=lambda: Session(engine),
        artifact_store=ArtifactStore(tmp_path / "artifacts"),
    )
    transport = _Transport()
    provider = HubGitContentProvider(
        registry=registry, transport=transport, payload_store=store
    )
    revision_digest = git_source_revision_digest(
        connector_type="github_repository",
        source_id="github-installation:example",
        commit_sha=COMMIT,
    )
    request = GitContentRequest(
        scope=scope,
        connector_type="github_repository",
        connection_ref=record.connection_ref,
        repository_identifier=record.repository,
        requested_ref="main",
        commit_sha=COMMIT,
        budgets=GitRepositoryBudgets(),
        transport_authorization=_authorization(),
        source_id=record.connection_ref,
        source_revision_digest=revision_digest,
    )
    metrics = provider.fetch(request)
    assert provider.inventory(request).manifest_digest == metrics.manifest_digest
    assert transport.fetches == 1

    connection_id = "conn_" + "c" * 64
    with Session(engine) as db:
        db.add(
            SourceConnectionDB(
                connection_id=connection_id,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                owner_id=scope.owner_id,
                connector_type="github",
                connection_identity_digest="d" * 64,
                display_name="example",
                sensitivity="internal",
                state="active",
                created_at_epoch=1,
                updated_at_epoch=1,
            )
        )
        db.add(
            SourceConnectionSelectorDB(
                connection_id=connection_id,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                owner_id=scope.owner_id,
                public_connector_type="github_repository",
                implementation_connector_type="github_repository",
                selector_kind="remote",
                selector_id=record.connection_ref,
                repository_identifier=record.repository,
                binding_digest="e" * 64,
                created_at_epoch=1,
            )
        )
        db.commit()
    descriptor = {
        "source_id": record.connection_ref,
        "source_type": "github_repository",
        "tenant_id": scope.tenant_id,
        "project_id": scope.project_id,
        "owner_id": scope.owner_id,
        "connection_id": connection_id,
        "github_authorization_ref": record.connection_ref,
        "repository": record.repository,
        "ref": "main",
    }
    admission = RemoteGitSourceAdmissionService(
        engine=engine,
        registry=registry,
        payload_store=store,
        revision_repository=SQLSourceControlRepository(engine),
        receipt_repository=SQLSourceAdmissionReceiptRepository(engine),
        budgets=SourceAdmissionBudgets(),
    )
    result = admission.scan_source(
        descriptor=descriptor,
        revision=SimpleNamespace(
            revision_digest=revision_digest,
            metadata={"commit_sha": COMMIT},
        ),
        inventory=SimpleNamespace(manifest_digest=metrics.manifest_digest),
    )
    authority = BoundSourceRevisionAuthority(
        tenant_id=scope.tenant_id,
        project_id=scope.project_id,
        connection_id=connection_id,
        source_revision_id=result["source_revision_id"],
        source_revision_digest=revision_digest,
        content_manifest_digest=metrics.manifest_digest,
        connector_type="github",
        source_id=record.connection_ref,
        admission_state="admitted",
        admission_digest=result["admission_digest"],
    )
    payload = RemoteGitBoundSourcePayloadAdapter(
        engine=engine, payload_store=store, registry=registry
    ).load_bound_revision_payload(authority)
    assert payload.connection_id == connection_id
    assert payload.payload_digest == result["payload_digest"]
    assert payload.records[0]["content"] == CONTENT.decode("utf-8")

    registry.set_authorization_state(
        scope=scope,
        connection_ref=record.connection_ref,
        repository=record.repository,
        authorization_state="revoked",
    )
    with pytest.raises(BoundSourceRevisionPlanningError):
        RemoteGitBoundSourcePayloadAdapter(
            engine=engine, payload_store=store, registry=registry
        ).load_bound_revision_payload(authority)
