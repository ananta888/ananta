"""Hub scan hook for governed registered-workspace admission."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Mapping

from sqlmodel import Session, select

from agent.db_models.source_control import (
    SourceConnectionDB,
    SourceConnectionSelectorDB,
    SourceRevisionDB,
)
from agent.services.source_admission_revision_coordinator import (
    SourceAdmissionRevisionCoordinator,
    SourceAdmissionRevisionRequest,
)
from agent.services.source_admission_service import SourceAdmissionBudgets


class RegisteredWorkspaceSourceAdmissionError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class RegisteredWorkspaceSourceAdmissionService:
    """Adapt the existing Hub scan operation to immutable admission."""

    def __init__(
        self,
        *,
        engine: Any,
        workspace_catalog: Any,
        workspace_connector: Any,
        coordinator: SourceAdmissionRevisionCoordinator,
        budgets: SourceAdmissionBudgets,
    ) -> None:
        self._engine = engine
        self._workspaces = workspace_catalog
        self._connector = workspace_connector
        self._coordinator = coordinator
        self._budgets = budgets

    def scan_source(
        self,
        *,
        descriptor: Mapping[str, object],
        revision: Any,
        inventory: Any,
    ) -> Mapping[str, object]:
        tenant_id = str(descriptor.get("tenant_id") or "").strip()
        project_id = str(descriptor.get("project_id") or "").strip()
        workspace_id = str(
            descriptor.get("workspace_id")
            or descriptor.get("source_id")
            or ""
        ).strip()
        connector_type = str(
            descriptor.get("connector_type") or "registered_workspace"
        ).strip()
        relative_path = str(descriptor.get("relative_path") or ".").strip()
        if not all((tenant_id, project_id, workspace_id, connector_type)):
            raise RegisteredWorkspaceSourceAdmissionError(
                "source_admission_descriptor_invalid"
            )
        with Session(self._engine) as session:
            selectors = session.exec(
                select(SourceConnectionSelectorDB).where(
                    SourceConnectionSelectorDB.tenant_id == tenant_id,
                    SourceConnectionSelectorDB.project_id == project_id,
                    SourceConnectionSelectorDB.public_connector_type
                    == connector_type,
                    SourceConnectionSelectorDB.selector_id == workspace_id,
                )
            ).all()
            selectors = [
                item
                for item in selectors
                if (item.relative_path or ".") == relative_path
            ]
            if len(selectors) != 1:
                raise RegisteredWorkspaceSourceAdmissionError(
                    "source_admission_connection_selector_required"
                )
            selector = selectors[0]
            connection = session.get(
                SourceConnectionDB, selector.connection_id
            )
        if (
            connection is None
            or connection.state != "active"
            or connection.tenant_id != tenant_id
            or connection.project_id != project_id
        ):
            raise RegisteredWorkspaceSourceAdmissionError(
                "source_admission_connection_inactive"
            )
        workspace = self._workspaces.get(
            workspace_id=workspace_id,
            tenant_id=tenant_id,
            project_id=project_id,
            owner_id=connection.owner_id,
        )
        if workspace is None:
            raise RegisteredWorkspaceSourceAdmissionError(
                "source_admission_workspace_unavailable"
            )
        snapshot = self._connector.inventory(
            tenant_id=tenant_id,
            project_id=project_id,
            workspace_id=workspace_id,
            relative_path=relative_path,
        )
        if (
            str(getattr(revision, "revision_digest", ""))
            != snapshot.revision_digest
            or str(getattr(inventory, "manifest_digest", ""))
            != snapshot.manifest_digest
        ):
            raise RegisteredWorkspaceSourceAdmissionError(
                "source_admission_refresh_snapshot_stale"
            )
        with Session(self._engine) as session:
            existing_revision = session.exec(
                select(SourceRevisionDB).where(
                    SourceRevisionDB.connection_id
                    == connection.connection_id,
                    SourceRevisionDB.revision_digest
                    == snapshot.revision_digest,
                )
            ).first()
        captured_at = (
            datetime.fromtimestamp(
                existing_revision.captured_at_epoch,
                tz=timezone.utc,
            )
            if existing_revision is not None
            else datetime.now(timezone.utc)
        )
        result = self._coordinator.admit(
            SourceAdmissionRevisionRequest(
                connection_id=connection.connection_id,
                tenant_id=tenant_id,
                project_id=project_id,
                owner_id=connection.owner_id,
                connector_type=connection.connector_type,
                sensitivity=connection.sensitivity,
                policy_digest=self._policy_digest(self._budgets),
                workspace=workspace,
                snapshot=snapshot,
                captured_at=captured_at,
            )
        )
        return {
            "status": "completed",
            "decision": result.decision.state.value,
            "reason_codes": list(result.decision.reason_codes),
            "admission_digest": result.decision.admission_digest,
            "inventory_evidence_digest": (
                result.decision.inventory_evidence_digest
            ),
            "scan_evidence_digest": result.decision.scan_evidence_digest,
            "file_count": result.scan_result.inventory.file_count,
            "total_bytes": result.scan_result.inventory.total_bytes,
        }

    @staticmethod
    def _policy_digest(budgets: SourceAdmissionBudgets) -> str:
        values = asdict(budgets)
        values["allowed_file_types"] = sorted(budgets.allowed_file_types)
        encoded = json.dumps(
            values,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "RegisteredWorkspaceSourceAdmissionError",
    "RegisteredWorkspaceSourceAdmissionService",
]
