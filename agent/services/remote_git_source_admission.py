"""Admission composition for immutable artifact-backed remote Git payloads."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import PurePosixPath
import time
from typing import Any, Mapping

from sqlmodel import Session, select

from agent.db_models.source_control import (
    SourceConnectionDB,
    SourceConnectionSelectorDB,
)
from agent.repositories.source_admission_receipt_repository import (
    SourceAdmissionCounters,
    SourceAdmissionReceiptDraft,
    SourceAdmissionReceiptPort,
)
from agent.services.augment.augment_secret_scanner import AugmentSecretScanner
from agent.services.hub_git_authorization_registry import (
    HubGitAuthorizationRegistryPort,
)
from agent.services.remote_source_payload_store import (
    SQLRemoteSourcePayloadStore,
    remote_source_prompt_injection_count,
    require_active_authorization,
)
from agent.services.source_admission_revision_coordinator import (
    SourceRevisionAppendPort,
)
from agent.services.source_admission_service import (
    SourceAdmissionBudgets,
    SourceInventoryEvidence,
    SourceScanEvidence,
    evaluate_source_admission,
)
from agent.sources.git_source_connector_common import GitSourceScope
from agent.sources.source_connectors import SourceConnectorError
from ananta_contracts.source_control import SourceRevision, derive_source_revision_id


_REMOTE_TYPES = frozenset({"generic_git", "github_repository"})
_CONTRACT_CONNECTOR_TYPES = {
    "generic_git": "git",
    "github_repository": "github",
}
class RemoteGitSourceAdmissionService:
    def __init__(
        self,
        *,
        engine: Any,
        registry: HubGitAuthorizationRegistryPort,
        payload_store: SQLRemoteSourcePayloadStore,
        revision_repository: SourceRevisionAppendPort,
        receipt_repository: SourceAdmissionReceiptPort,
        budgets: SourceAdmissionBudgets,
        clock=time.time,
    ) -> None:
        self._engine = engine
        self._registry = registry
        self._payloads = payload_store
        self._revisions = revision_repository
        self._receipts = receipt_repository
        self._budgets = budgets
        self._clock = clock
        self._secrets = AugmentSecretScanner()
        self._policy_digest = hashlib.sha256(
            json.dumps(
                {
                    "allow_archives": budgets.allow_archives,
                    "allow_binary": budgets.allow_binary,
                    "allow_prompt_injection": budgets.allow_prompt_injection,
                    "allow_secrets": budgets.allow_secrets,
                    "allowed_file_types": sorted(budgets.allowed_file_types),
                    "max_archive_expansion_ratio": budgets.max_archive_expansion_ratio,
                    "max_file_bytes": budgets.max_file_bytes,
                    "max_files": budgets.max_files,
                    "max_total_bytes": budgets.max_total_bytes,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    def scan_source(
        self,
        *,
        descriptor: Mapping[str, Any],
        revision: Any,
        inventory: Any,
    ) -> Mapping[str, Any]:
        connector_type = str(descriptor.get("source_type") or "").strip()
        if connector_type not in _REMOTE_TYPES:
            raise SourceConnectorError("source_scan_connector_unsupported")
        scope = GitSourceScope.from_descriptor(descriptor)
        connection_ref = str(
            descriptor.get("github_authorization_ref")
            or descriptor.get("remote_id")
            or ""
        ).strip()
        repository = (
            str(descriptor.get("repository") or "").strip() or None
        )
        _record, authorization_digest = require_active_authorization(
            registry=self._registry,
            scope=scope,
            connection_ref=connection_ref,
            repository_identifier=repository,
        )
        metadata = getattr(revision, "metadata", {})
        commit_sha = str(
            metadata.get("commit_sha") if isinstance(metadata, Mapping) else ""
        ).strip()
        payload = self._payloads.load_for_revision(
            scope=scope,
            connector_type=connector_type,
            source_id=str(descriptor.get("source_id") or "").strip(),
            connection_ref=connection_ref,
            repository_identifier=repository,
            commit_sha=commit_sha,
            source_revision_digest=str(revision.revision_digest),
            manifest_digest=str(inventory.manifest_digest),
            authorization_binding_digest=authorization_digest,
        )
        selector, connection = self._connection(descriptor, payload)
        contract_connector_type = _CONTRACT_CONNECTOR_TYPES[connector_type]
        if connection.connector_type != contract_connector_type:
            raise SourceConnectorError("source_connection_connector_mismatch")
        source_revision_id = derive_source_revision_id(
            connection_id=connection.connection_id,
            revision_digest=payload.source_revision_digest,
        )
        file_types = Counter(
            (PurePosixPath(item.relative_path).suffix.lower().lstrip(".") or "text")
            for item in payload.files
        )
        secret_findings = 0
        injection_findings = 0
        for item in payload.files:
            if not self._secrets.scan_and_redact_text(item.content).clean:
                secret_findings += 1
            injection_findings += remote_source_prompt_injection_count(
                item.content
            )
        evidence = SourceInventoryEvidence(
            revision_digest=payload.source_revision_digest,
            manifest_digest=payload.manifest_digest,
            file_count=len(payload.files),
            total_bytes=sum(item.byte_size for item in payload.files),
            largest_file_bytes=max(
                (item.byte_size for item in payload.files), default=0
            ),
            archive_expansion_ratio=1.0,
            file_type_counts=dict(file_types),
        )
        scan = SourceScanEvidence(
            revision_digest=payload.source_revision_digest,
            manifest_digest=payload.manifest_digest,
            scanner_id="hub.remote-git-artifact",
            scanner_version="1",
            completed=True,
            secret_findings=secret_findings,
            injection_findings=injection_findings,
        )
        decision = evaluate_source_admission(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            source_revision_id=source_revision_id,
            revision_digest=payload.source_revision_digest,
            policy_digest=self._policy_digest,
            inventory=evidence,
            scan=scan,
            budgets=self._budgets,
        )
        captured_at = datetime.fromtimestamp(
            float(self._clock()), tz=timezone.utc
        )
        persisted = self._revisions.append_revision(
            SourceRevision.create(
                connection_id=connection.connection_id,
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                owner_id=scope.owner_id,
                connector_type=contract_connector_type,
                sensitivity=connection.sensitivity,
                revision_token=f"git-commit:{payload.commit_sha}",
                revision_digest=payload.source_revision_digest,
                content_manifest_id=f"manifest_{payload.manifest_digest}",
                content_manifest_digest=payload.manifest_digest,
                admission_state=decision.state.value,
                captured_at=captured_at,
            )
        )
        receipt = self._receipts.get_by_admission_digest(
            tenant_id=scope.tenant_id,
            project_id=scope.project_id,
            admission_digest=decision.admission_digest,
        )
        if receipt is None:
            receipt = self._receipts.append(
                SourceAdmissionReceiptDraft(
                    tenant_id=scope.tenant_id,
                    project_id=scope.project_id,
                    source_revision_id=source_revision_id,
                    decision_state=decision.state.value,
                    reason_codes=tuple(decision.reason_codes),
                    revision_digest=payload.source_revision_digest,
                    manifest_digest=payload.manifest_digest,
                    policy_digest=self._policy_digest,
                    inventory_evidence_digest=decision.inventory_evidence_digest,
                    scan_evidence_digest=decision.scan_evidence_digest,
                    admission_digest=decision.admission_digest,
                    counters=SourceAdmissionCounters(
                        file_count=evidence.file_count,
                        total_bytes=evidence.total_bytes,
                        largest_file_bytes=evidence.largest_file_bytes,
                        archive_expansion_ratio=evidence.archive_expansion_ratio,
                        secret_findings=scan.secret_findings,
                        injection_findings=scan.injection_findings,
                    ),
                    evaluated_at_epoch=float(self._clock()),
                )
            )
        self._payloads.bind_revision(
            payload=payload,
            connection_id=connection.connection_id,
            source_revision_id=source_revision_id,
        )
        return {
            "status": "completed",
            "source_revision_id": source_revision_id,
            "payload_digest": payload.payload_digest,
            "admission_state": decision.state.value,
            "admission_digest": decision.admission_digest,
            "reason_codes": list(decision.reason_codes),
            "scanner_id": scan.scanner_id,
        }

    def _connection(self, descriptor: Mapping[str, Any], payload: Any):
        supplied_id, supplied_binding_digest = (
            self._bound_connection_coordinates(descriptor)
        )
        with Session(self._engine) as db:
            selector = (
                db.get(SourceConnectionSelectorDB, supplied_id)
                if supplied_id
                else None
            )
            if selector is None:
                matches = db.exec(
                    select(SourceConnectionSelectorDB).where(
                        SourceConnectionSelectorDB.tenant_id == payload.tenant_id,
                        SourceConnectionSelectorDB.project_id == payload.project_id,
                        SourceConnectionSelectorDB.owner_id == payload.owner_id,
                        SourceConnectionSelectorDB.public_connector_type
                        == payload.connector_type,
                    )
                ).all()
                selector = next(
                    (
                        item
                        for item in matches
                        if item.selector_id
                        in {payload.source_id, payload.connection_ref}
                        and (item.repository_identifier or None)
                        == payload.repository_identifier
                    ),
                    None,
                )
            connection = (
                db.get(SourceConnectionDB, selector.connection_id)
                if selector is not None
                else None
            )
        if (
            selector is None
            or connection is None
            or (
                supplied_binding_digest is not None
                and selector.binding_digest != supplied_binding_digest
            )
            or selector.tenant_id != payload.tenant_id
            or selector.project_id != payload.project_id
            or selector.owner_id != payload.owner_id
            or selector.implementation_connector_type
            != payload.connector_type
            or selector.selector_id != payload.connection_ref
            or (selector.repository_identifier or None)
            != payload.repository_identifier
            or connection.state != "active"
            or connection.disabled_at_epoch is not None
            or connection.tombstoned_at_epoch is not None
            or connection.tenant_id != payload.tenant_id
            or connection.project_id != payload.project_id
            or connection.owner_id != payload.owner_id
        ):
            raise SourceConnectorError("source_connection_inactive")
        return selector, connection

    @staticmethod
    def _bound_connection_coordinates(
        descriptor: Mapping[str, Any],
    ) -> tuple[str, str | None]:
        direct_id = str(descriptor.get("connection_id") or "").strip()
        extensions = descriptor.get("extensions")
        source_control = (
            extensions.get("source_control")
            if isinstance(extensions, Mapping)
            else None
        )
        if source_control is None:
            return direct_id, None
        if not isinstance(source_control, Mapping):
            raise SourceConnectorError("source_connection_binding_invalid")
        bound_id = str(source_control.get("connection_id") or "").strip()
        binding_digest = str(
            source_control.get("binding_digest") or ""
        ).strip()
        if (
            not bound_id
            or len(binding_digest) != 64
            or (direct_id and direct_id != bound_id)
        ):
            raise SourceConnectorError("source_connection_binding_invalid")
        return bound_id, binding_digest


class SourceScanServiceRouter:
    def __init__(self, services: Mapping[str, Any]) -> None:
        self._services = dict(services)

    def scan_source(self, **kwargs: Any) -> Mapping[str, Any]:
        descriptor = kwargs.get("descriptor")
        connector_type = (
            str(descriptor.get("source_type") or "").strip()
            if isinstance(descriptor, Mapping)
            else ""
        )
        service = self._services.get(connector_type)
        if service is None:
            raise SourceConnectorError("source_scan_connector_unsupported")
        return service.scan_source(**kwargs)


__all__ = ["RemoteGitSourceAdmissionService", "SourceScanServiceRouter"]
