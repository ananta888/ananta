from __future__ import annotations

import json

import pytest

from agent.services.source_admission_service import (
    SourceAdmissionBudgets,
    SourceInventoryEvidence,
    SourceScanEvidence,
)
from agent.sources.registered_workspace_connector import (
    RegisteredWorkspace,
    RegisteredWorkspaceConnector,
)
from agent.sources.registered_workspace_source_adapter import (
    RegisteredWorkspaceSourceAdapter,
)
from agent.sources.source_admission_gate import (
    EvaluatingSourceAdmissionGate,
    SourceIndexAdmissionError,
)
from agent.sources.source_control_connector_composition import (
    build_source_control_connector_extensions,
)
from agent.sources.source_connectors import (
    ConnectorHealth,
    ConnectorRefreshRequest,
    ConnectorRegistry,
    DescriptorConnectorAdapter,
    SourceConnector,
    SourceConnectorError,
    SourceInventory,
    SourceRevisionResolution,
    build_default_connector_registry,
)
from agent.sources.source_refresh_service import SourceRefreshService


REVISION_DIGEST = "a" * 64
MANIFEST_DIGEST = "b" * 64
POLICY_DIGEST = "c" * 64


class WorkspaceCatalog:
    def __init__(self, workspace):
        self.workspace = workspace

    def get(self, *, workspace_id, tenant_id, project_id):
        if (
            workspace_id,
            tenant_id,
            project_id,
        ) == (
            self.workspace.workspace_id,
            self.workspace.tenant_id,
            self.workspace.project_id,
        ):
            return self.workspace
        return None


class NamedAdapter:
    def __init__(self, connector_type: str) -> None:
        self._adapter = DescriptorConnectorAdapter(connector_type)

    def connector(self):
        return self._adapter.connector()


class SourceCatalog:
    def __init__(self, descriptor):
        self.descriptor = descriptor

    def get_source(self, source_id):
        if source_id == self.descriptor["source_id"]:
            return dict(self.descriptor)
        return None

    def list_sources(self, *, include_disabled=False):
        del include_disabled
        return [dict(self.descriptor)]


class RecordingRegistry(ConnectorRegistry):
    def __init__(self, connector):
        super().__init__([connector])
        self.get_calls = []

    def get(self, connector_type):
        self.get_calls.append(connector_type)
        return super().get(connector_type)


class RecordingConnector:
    connector_type = "recording"

    def __init__(self):
        self.revision_calls = 0
        self.inventory_calls = 0
        self.refresh_calls = 0
        self.health_calls = 0

    def validate(self, descriptor):
        del descriptor
        return ()

    def resolve_revision(self, descriptor):
        del descriptor
        self.revision_calls += 1
        return SourceRevisionResolution(
            revision_digest=REVISION_DIGEST,
            immutable_ref=f"descriptor-sha256:{REVISION_DIGEST}",
            metadata={},
        )

    def inventory(self, descriptor):
        del descriptor
        self.inventory_calls += 1
        return SourceInventory(
            item_count=1,
            total_bytes=7,
            exclusions=(),
            manifest_digest=MANIFEST_DIGEST,
        )

    def refresh(self, descriptor, request):
        del descriptor, request
        self.refresh_calls += 1
        return {"status": "ok"}

    def health(self, descriptor):
        del descriptor
        self.health_calls += 1
        return ConnectorHealth(status="healthy")

    def connector(self):
        return SourceConnector(
            connector_type=self.connector_type,
            validator=self,
            revision_resolver=self,
            inventory_provider=self,
            refresher=self,
            health_provider=self,
        )


def build_refresh_service(*, admission_gate=None):
    adapter = RecordingConnector()
    connector_registry = RecordingRegistry(adapter.connector())
    descriptor = {
        "source_id": "source-recording",
        "source_type": "recording",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "enabled": True,
    }
    service = SourceRefreshService(
        registry=SourceCatalog(descriptor),
        snapshots=object(),
        cache=object(),
        keycloak_fetcher=object(),
        wikimedia_downloader=object(),
        connector_registry=connector_registry,
        admission_gate=admission_gate,
    )
    return service, adapter, connector_registry


def test_default_composition_keeps_legacy_and_adds_governed_connectors(
    tmp_path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace_connector = RegisteredWorkspaceConnector(
        catalog=WorkspaceCatalog(
            RegisteredWorkspace(
                workspace_id="workspace-1",
                tenant_id="tenant-1",
                project_id="project-1",
                root=root,
                enabled=True,
                read_only=True,
            )
        )
    )
    extensions = build_source_control_connector_extensions(
        github_repository=NamedAdapter("github_repository"),
        generic_git=NamedAdapter("generic_git"),
        registered_workspace=workspace_connector,
    )

    registry = build_default_connector_registry(
        keycloak_fetcher=object(),
        wikimedia_downloader=object(),
        cache=object(),
        snapshots=object(),
        additional_connectors=extensions,
    )

    assert registry.list_types() == (
        "generic_git",
        "github_repository",
        "inline_text",
        "keycloak_docs",
        "local_directory",
        "open_notebook",
        "open_notebook_export",
        "registered_workspace",
        "text",
        "wiki",
        "wikimedia_dump",
    )


@pytest.mark.parametrize(
    "connector_type",
    ("registered_workspace", "local_directory"),
)
def test_workspace_adapter_never_returns_host_paths(
    tmp_path,
    connector_type,
) -> None:
    root = tmp_path / "host-only-root"
    root.mkdir()
    (root / "readme.txt").write_text("bounded", encoding="utf-8")
    low_level = RegisteredWorkspaceConnector(
        catalog=WorkspaceCatalog(
            RegisteredWorkspace(
                workspace_id="workspace-1",
                tenant_id="tenant-1",
                project_id="project-1",
                root=root,
                enabled=True,
                read_only=True,
            )
        )
    )
    adapter = RegisteredWorkspaceSourceAdapter(
        workspace_connector=low_level,
        connector_type=connector_type,
    )
    descriptor = {
        "source_id": "source-workspace",
        "source_type": connector_type,
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "workspace_id": "workspace-1",
        "relative_path": ".",
    }

    outputs = (
        adapter.resolve_revision(descriptor),
        adapter.inventory(descriptor),
        adapter.refresh(descriptor, ConnectorRefreshRequest()),
        adapter.health(descriptor),
    )

    assert str(root) not in json.dumps(outputs, default=lambda item: vars(item))
    assert adapter.validate({**descriptor, "host_path": str(root)}) == (
        "workspace_host_path_forbidden",
    )


def test_all_source_operations_select_the_connector_through_registry() -> None:
    service, adapter, registry = build_refresh_service()

    service.resolve_revision(source_id="source-recording")
    service.inventory(source_id="source-recording")
    service.health(source_id="source-recording")
    service.refresh_source(source_id="source-recording")

    assert registry.get_calls == ["recording"] * 4
    assert adapter.revision_calls == 1
    assert adapter.inventory_calls == 1
    assert adapter.health_calls == 1
    assert adapter.refresh_calls == 1


def admission_evidence(*, secret_findings=0):
    inventory = SourceInventoryEvidence(
        revision_digest=REVISION_DIGEST,
        manifest_digest=MANIFEST_DIGEST,
        file_count=1,
        total_bytes=7,
        largest_file_bytes=7,
        archive_expansion_ratio=1.0,
        file_type_counts={"txt": 1},
    )
    scan = SourceScanEvidence(
        revision_digest=REVISION_DIGEST,
        manifest_digest=MANIFEST_DIGEST,
        scanner_id="scanner-1",
        scanner_version="1.0",
        completed=True,
        secret_findings=secret_findings,
    )
    return inventory, scan


def test_index_release_has_no_admission_gate_bypass() -> None:
    service, adapter, _ = build_refresh_service()
    inventory, scan = admission_evidence()

    with pytest.raises(SourceConnectorError) as exc:
        service.authorize_index_release(
            source_id="source-recording",
            source_revision_id="revision-1",
            policy_digest=POLICY_DIGEST,
            inventory_evidence=inventory,
            scan_evidence=scan,
        )

    assert exc.value.reason_code == "source_admission_gate_required"
    assert adapter.revision_calls == 0
    assert adapter.inventory_calls == 0


def test_index_release_requires_admitted_revision_bound_evidence() -> None:
    gate = EvaluatingSourceAdmissionGate(budgets=SourceAdmissionBudgets())
    service, adapter, registry = build_refresh_service(admission_gate=gate)
    inventory, scan = admission_evidence()

    decision = service.authorize_index_release(
        source_id="source-recording",
        source_revision_id="revision-1",
        policy_digest=POLICY_DIGEST,
        inventory_evidence=inventory,
        scan_evidence=scan,
    )

    assert decision.state.value == "admitted"
    assert registry.get_calls == ["recording"]
    assert adapter.revision_calls == 1
    assert adapter.inventory_calls == 1
    assert adapter.refresh_calls == 0


def test_blocked_evidence_cannot_release_index_or_create_connector_work() -> None:
    gate = EvaluatingSourceAdmissionGate(budgets=SourceAdmissionBudgets())
    service, adapter, _ = build_refresh_service(admission_gate=gate)
    inventory, scan = admission_evidence(secret_findings=1)

    with pytest.raises(SourceIndexAdmissionError) as exc:
        service.authorize_index_release(
            source_id="source-recording",
            source_revision_id="revision-1",
            policy_digest=POLICY_DIGEST,
            inventory_evidence=inventory,
            scan_evidence=scan,
        )

    assert exc.value.reason_code == "source_admission_blocked"
    assert exc.value.decision is not None
    assert exc.value.decision.reason_codes == ("secret_detected",)
    assert adapter.refresh_calls == 0
