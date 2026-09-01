"""Hub-owned proposal, validation and automatic promotion saga."""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from collections.abc import Mapping
from typing import Any

from agent.services.spreadsheet_actual_diff_service import SpreadsheetActualDiffService
from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_execution_ports import SpreadsheetExecutionPort
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_repository_port import SpreadsheetDocumentRepositoryPort
from agent.services.spreadsheet_store import SpreadsheetStoreConflict
from agent.services.spreadsheet_validation_reference_port import (
    SpreadsheetValidationReferenceRepositoryPort,
)
from agent.services.spreadsheet_validator_engine import SpreadsheetValidatorEngine
from agent.services.spreadsheet_viewport_service import SpreadsheetViewportService
from ananta_contracts.spreadsheet_studio import (
    SpreadsheetProposalV1,
    WorkbookSnapshotV1,
    canonical_digest,
    require_id,
)
from ananta_contracts.spreadsheet_studio_v2 import (
    execution_snapshot,
    merge_execution_candidate,
    parse_workbook_snapshot,
)


class SpreadsheetSagaService:
    def __init__(
        self,
        store: SpreadsheetDocumentRepositoryPort,
        *,
        policy: SpreadsheetPolicy,
        executor: SpreadsheetExecutionPort,
        artifact_store: SpreadsheetArtifactStore | None = None,
        validator_engine: SpreadsheetValidatorEngine | None = None,
        actual_diff_service: SpreadsheetActualDiffService | None = None,
        viewport_service: SpreadsheetViewportService | None = None,
        validation_references: SpreadsheetValidationReferenceRepositoryPort | None = None,
        training_available: bool = False,
    ) -> None:
        policy.validate()
        self._store = store
        self._policy = policy
        self._executor = executor
        self._artifacts = artifact_store
        self._validators = validator_engine or SpreadsheetValidatorEngine(validation_references)
        self._actual_diff = actual_diff_service or SpreadsheetActualDiffService()
        self._viewports = viewport_service or SpreadsheetViewportService()
        self._validation_references = validation_references
        self._training_available = bool(training_available)

    def capabilities(self) -> dict[str, Any]:
        try:
            capability = dict(self._executor.capability)
        except RuntimeError as exc:
            capability = {
                "state": "unavailable",
                "reason_code": str(getattr(exc, "reason_code", str(exc))),
            }
        available = self._policy.enabled and capability.get("state") == "available"
        return {
            "schema": "ananta.spreadsheet-studio-capability.v1",
            "available": available,
            "state": "available" if available else "disabled",
            "mode": self._policy.mode,
            "automatic_promotion_enabled": self._policy.automatic_promotion_enabled,
            "executor": capability,
            "supported_formats": list(capability.get("supported_formats") or ["canonical_snapshot"]),
            "supported_snapshot_schemas": [
                "ananta.spreadsheet-workbook-snapshot.v1",
                "ananta.spreadsheet-workbook-snapshot.v2",
            ],
            "actual_diff_schema": "ananta.spreadsheet-actual-diff.v1",
            "validation_result_schema": "ananta.spreadsheet-validation-result.v2",
            "validation_reference_schema": "ananta.spreadsheet-validation-reference.v1",
            "libreoffice_fidelity_verified": bool(
                capability.get("engine") == "libreoffice-calc" and capability.get("production_fidelity") is True
            ),
            "training_available": self._training_available,
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }

    def create_document(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        title: str,
        snapshot: Mapping[str, Any],
        document_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._policy.enabled:
            raise PermissionError("spreadsheet_studio_disabled")
        parsed = parse_workbook_snapshot(snapshot)
        normalized_title = str(title or "").strip()
        if not 1 <= len(normalized_title) <= 200:
            raise ValueError("spreadsheet_document_title_invalid")
        value = {
            "schema": "ananta.spreadsheet-document-version.v1",
            "document_id": require_id(document_id or f"spreadsheet-{uuid.uuid4()}", "document_id"),
            "owner_id": require_id(owner_id, "owner_id"),
            "title": normalized_title,
            "snapshot": parsed.to_dict(),
            "snapshot_digest": parsed.digest,
            "state": "published",
            "created_at": time.time(),
            "source_refs": [],
            "run_refs": [],
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }
        return self._store.create_document(tenant_id, value)

    def import_document(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        title: str,
        filename: str,
        media_type: str,
        content: bytes,
        document_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._policy.enabled:
            raise PermissionError("spreadsheet_studio_disabled")
        if self._artifacts is None or not hasattr(self._executor, "import_document"):
            raise RuntimeError("spreadsheet_document_import_unavailable")
        normalized_title = str(title or "").strip()
        if not 1 <= len(normalized_title) <= 200:
            raise ValueError("spreadsheet_document_title_invalid")
        normalized_document_id = require_id(document_id or f"spreadsheet-{uuid.uuid4()}", "document_id")
        version_id = f"version-{hashlib.sha256(normalized_document_id.encode()).hexdigest()[:24]}-1"
        imported = dict(
            self._executor.import_document(  # type: ignore[attr-defined]
                content=content,
                filename=filename,
                media_type=media_type,
                document_version_id=version_id,
            )
        )
        if imported.get("schema") != "ananta.spreadsheet-import-result.v1":
            raise ValueError("spreadsheet_import_result_invalid")
        snapshot = WorkbookSnapshotV1.from_mapping(imported.get("snapshot") or {})
        if snapshot.digest != imported.get("snapshot_digest"):
            raise ValueError("spreadsheet_import_snapshot_digest_invalid")
        source = imported.get("source")
        if not isinstance(source, Mapping) or hashlib.sha256(content).hexdigest() != source.get("sha256"):
            raise ValueError("spreadsheet_import_source_digest_invalid")
        stored = self._artifacts.store(
            tenant_id=tenant_id,
            content=content,
            format=str(source.get("format") or ""),
            media_type=str(source.get("media_type") or ""),
            expected_sha256=str(source.get("sha256") or ""),
        )
        value = {
            "schema": "ananta.spreadsheet-document-version.v1",
            "document_id": normalized_document_id,
            "owner_id": require_id(owner_id, "owner_id"),
            "title": normalized_title,
            "snapshot": snapshot.to_dict(),
            "snapshot_digest": snapshot.digest,
            "state": "published",
            "created_at": time.time(),
            "source_artifact": {
                "artifact_id": stored.artifact_id,
                "sha256": stored.sha256,
                "size_bytes": stored.size_bytes,
                "format": stored.format,
                "media_type": stored.media_type,
            },
            "unsupported_objects": list(imported.get("unsupported_objects") or []),
            "engine": imported.get("engine"),
            "engine_version": imported.get("engine_version"),
            "production_fidelity": bool(imported.get("production_fidelity")),
            "source_refs": [],
            "run_refs": [],
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }
        return self._store.create_document(tenant_id, value)

    def execute_proposal(self, *, tenant_id: str, principal_id: str, proposal: Mapping[str, Any]) -> dict[str, Any]:
        prepared = self.prepare_proposal_execution(
            tenant_id=tenant_id,
            principal_id=principal_id,
            proposal=proposal,
        )
        if prepared.get("completed_result") is not None:
            return dict(prepared["completed_result"])
        parsed = SpreadsheetProposalV1.from_mapping(prepared["proposal"])
        document = dict(prepared["document"])
        snapshot = execution_snapshot(document["snapshot"])
        source_input = self._source_execution_input(
            tenant_id=tenant_id,
            document=document,
            document_id=parsed.document_id,
        )
        execution = dict(
            self._executor.dry_run(
                snapshot=snapshot.to_dict(),
                actions=parsed.actions,
                **({"source_artifact": source_input} if source_input is not None else {}),
            )
        )
        return self.finalize_proposal_execution(
            tenant_id=tenant_id,
            prepared=prepared,
            execution=execution,
        )

    def prepare_proposal_execution(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        proposal: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate a proposal and freeze the exact Hub-owned Worker assignment."""

        parsed = SpreadsheetProposalV1.from_mapping(proposal)
        existing = self._store.get_proposal(tenant_id, parsed.proposal_id)
        if existing is not None:
            if existing.get("proposal_digest") != parsed.digest:
                raise SpreadsheetStoreConflict("spreadsheet_proposal_replay_conflict")
            return {
                "schema": "ananta.spreadsheet-execution-assignment.v1",
                "proposal": parsed.to_dict(),
                "proposal_digest": parsed.digest,
                "completed_result": {**existing, "replayed": True},
            }
        document = self._store.get_document(tenant_id, parsed.document_id)
        if document["owner_id"] != principal_id:
            raise PermissionError("spreadsheet_document_owner_required")
        if document["version"] != parsed.expected_version:
            raise SpreadsheetStoreConflict("spreadsheet_document_version_conflict")
        if document["snapshot_digest"] != parsed.base_snapshot_digest:
            raise SpreadsheetStoreConflict("spreadsheet_snapshot_digest_conflict")
        if document.get("unsupported_objects"):
            raise PermissionError("spreadsheet_document_unsupported_semantics")
        rich_snapshot = parse_workbook_snapshot(document["snapshot"])
        snapshot = execution_snapshot(rich_snapshot)
        if getattr(rich_snapshot, "value", {}).get("unsupported_objects"):
            raise PermissionError("spreadsheet_document_unsupported_semantics")
        self._policy.admit(snapshot, parsed)
        assignment = {
            "schema": "ananta.spreadsheet-execution-assignment.v1",
            "tenant_id": tenant_id,
            "principal_id": principal_id,
            "proposal": parsed.to_dict(),
            "proposal_digest": parsed.digest,
            "document": document,
            "base_snapshot_digest": rich_snapshot.digest,
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }
        assignment["assignment_digest"] = self._assignment_digest(assignment)
        return assignment

    def finalize_proposal_execution(
        self,
        *,
        tenant_id: str,
        prepared: Mapping[str, Any],
        execution: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Validate and atomically promote one digest-bound Worker result."""

        assignment = dict(prepared)
        supplied_assignment_digest = str(assignment.pop("assignment_digest", ""))
        if not supplied_assignment_digest or supplied_assignment_digest != self._assignment_digest(assignment):
            raise ValueError("spreadsheet_assignment_digest_invalid")
        if assignment.get("tenant_id") != tenant_id:
            raise PermissionError("spreadsheet_assignment_tenant_mismatch")
        parsed = SpreadsheetProposalV1.from_mapping(assignment.get("proposal") or {})
        if parsed.digest != assignment.get("proposal_digest"):
            raise ValueError("spreadsheet_assignment_proposal_digest_invalid")
        document = dict(assignment.get("document") or {})
        if (
            document.get("document_id") != parsed.document_id
            or document.get("version") != parsed.expected_version
            or document.get("snapshot_digest") != parsed.base_snapshot_digest
        ):
            raise ValueError("spreadsheet_assignment_document_binding_invalid")
        execution = dict(execution)
        candidate_artifact = self._store_result_artifact(tenant_id=tenant_id, execution=execution)
        execution_candidate = WorkbookSnapshotV1.from_mapping(execution["candidate_snapshot"])
        if execution_candidate.digest != execution.get("candidate_snapshot_digest"):
            raise ValueError("spreadsheet_execution_digest_invalid")
        candidate = merge_execution_candidate(
            base=document["snapshot"],
            candidate=execution_candidate.to_dict(),
            actions=parsed.actions,
            engine_name=str(execution.get("engine") or "unknown-engine"),
            engine_version=str(execution.get("engine_version") or "unknown-version"),
        )
        complete_diff = self._actual_diff.complete_items(
            before=document["snapshot"],
            after=candidate.to_dict(),
            execution_diff=execution["diff"],
            actions=parsed.actions,
        )
        actual_diff = self._actual_diff.paginate(complete_diff)
        validation_diff = {**actual_diff, "items": complete_diff}
        validation = self._validators.validate(
            candidate.to_dict(),
            parsed.validators,
            tenant_id=tenant_id,
            actual_diff=validation_diff,
            bindings={
                "document_digest": canonical_digest(
                    {
                        "tenant_id": tenant_id,
                        "document_id": parsed.document_id,
                        "version": parsed.expected_version,
                        "snapshot_digest": parsed.base_snapshot_digest,
                    }
                ),
                "task_digest": parsed.digest,
                "engine_digest": canonical_digest(
                    {
                        "engine": str(execution.get("engine") or "unknown-engine"),
                        "engine_version": str(execution.get("engine_version") or "unknown-version"),
                    }
                ),
                "recalc_digest": canonical_digest(_candidate_recalc_profile(candidate.to_dict())),
                "policy_digest": canonical_digest(
                    {
                        "enabled": self._policy.enabled,
                        "mode": self._policy.mode,
                        "automatic_promotion_enabled": self._policy.automatic_promotion_enabled,
                        "max_actions": self._policy.max_actions,
                        "max_affected_cells": self._policy.max_affected_cells,
                    }
                ),
            },
        )
        reasons = list(validation["reason_codes"])
        promote = bool(parsed.automatic_promotion and self._policy.automatic_promotion_enabled and validation["passed"])
        state = "promoted" if promote else ("candidate_ready" if validation["passed"] else "rejected")
        result = {
            "schema": "ananta.spreadsheet-proposal-result.v1",
            "tenant_id": tenant_id,
            "proposal_id": parsed.proposal_id,
            "proposal_digest": parsed.digest,
            "document_id": parsed.document_id,
            "base_version": parsed.expected_version,
            "base_snapshot_digest": parsed.base_snapshot_digest,
            "actions": [dict(action) for action in parsed.actions],
            "candidate_snapshot": candidate.to_dict(),
            "candidate_snapshot_digest": candidate.digest,
            "diff": execution["diff"],
            "actual_diff": actual_diff,
            "validation": validation,
            "state": state,
            "reason_codes": reasons,
            "automatic_decision": True,
            "production_fidelity": bool(execution.get("production_fidelity")),
            "candidate_artifact": candidate_artifact,
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }
        result["promoted_version"] = None
        if parsed.automatic_promotion and not self._policy.automatic_promotion_enabled:
            result["reason_codes"] = sorted({*result["reason_codes"], "spreadsheet_automatic_promotion_disabled"})
        promoted_document = (
            {
                **document,
                "snapshot": candidate.to_dict(),
                "snapshot_digest": candidate.digest,
                "state": "published",
                "created_at": time.time(),
                **({"published_artifact": candidate_artifact} if candidate_artifact is not None else {}),
            }
            if promote
            else None
        )
        return self._store.finalize_proposal(
            tenant_id,
            parsed.proposal_id,
            result,
            document_id=parsed.document_id,
            expected_version=parsed.expected_version,
            promoted_document=promoted_document,
        )

    def _source_execution_input(
        self,
        *,
        tenant_id: str,
        document: Mapping[str, Any],
        document_id: str,
    ) -> dict[str, Any] | None:
        source = document.get("source_artifact")
        if not isinstance(source, Mapping):
            return None
        if self._artifacts is None:
            raise RuntimeError("spreadsheet_artifact_store_unavailable")
        return {
            "content": self._artifacts.read(
                tenant_id=tenant_id,
                sha256=str(source.get("sha256") or ""),
                format=str(source.get("format") or ""),
            ),
            "filename": f"{document_id}.{source.get('format')}",
            "media_type": str(source.get("media_type") or ""),
            "sha256": str(source.get("sha256") or ""),
        }

    @staticmethod
    def _assignment_digest(value: Mapping[str, Any]) -> str:
        return canonical_digest(value)

    def get_document(self, *, tenant_id: str, document_id: str, principal_id: str) -> dict[str, Any]:
        value = self._store.get_document(tenant_id, document_id)
        if value["owner_id"] != principal_id:
            raise PermissionError("spreadsheet_document_owner_required")
        return value

    def get_viewport(
        self,
        *,
        tenant_id: str,
        document_id: str,
        principal_id: str,
        sheet_id: str,
        start: str,
        end: str,
        offset: int = 0,
        limit: int = 1_000,
    ) -> dict[str, Any]:
        document = self.get_document(
            tenant_id=tenant_id,
            document_id=document_id,
            principal_id=principal_id,
        )
        return self._viewports.project(
            snapshot=document["snapshot"],
            sheet_id=sheet_id,
            start=start,
            end=end,
            offset=offset,
            limit=limit,
        )

    def get_version_viewport(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version: int,
        principal_id: str,
        sheet_id: str,
        start: str,
        end: str,
        offset: int = 0,
        limit: int = 1_000,
    ) -> dict[str, Any]:
        """Project an immutable historical version without exposing its full snapshot."""

        document = self.get_version(
            tenant_id=tenant_id,
            document_id=document_id,
            version=version,
            principal_id=principal_id,
        )
        return self._viewports.project(
            snapshot=document["snapshot"],
            sheet_id=sheet_id,
            start=start,
            end=end,
            offset=offset,
            limit=limit,
        )

    def create_validation_reference(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        reference_id: str,
        document_id: str,
        version: int,
    ) -> dict[str, Any]:
        if self._validation_references is None:
            raise RuntimeError("spreadsheet_validation_reference_store_unavailable")
        document = self.get_version(
            tenant_id=tenant_id,
            document_id=document_id,
            version=version,
            principal_id=principal_id,
        )
        snapshot = parse_workbook_snapshot(document["snapshot"])
        value = {
            "schema": "ananta.spreadsheet-validation-reference.v1",
            "reference_id": require_id(reference_id, "reference_id"),
            "document_id": require_id(document_id, "document_id"),
            "document_version": int(version),
            "owner_id": require_id(principal_id, "principal_id"),
            "tenant_digest": canonical_digest({"tenant_id": tenant_id}),
            "snapshot_schema": snapshot.to_dict()["schema"],
            "snapshot_digest": snapshot.digest,
            "snapshot": snapshot.to_dict(),
            "source_grounding_verified": False,
            "human_intervention_required": False,
        }
        value["reference_digest"] = canonical_digest(value)
        return self._validation_references.create_reference(tenant_id, value)

    def get_validation_reference(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        reference_id: str,
    ) -> dict[str, Any]:
        if self._validation_references is None:
            raise RuntimeError("spreadsheet_validation_reference_store_unavailable")
        value = self._validation_references.get_reference(tenant_id, reference_id)
        if value["owner_id"] != principal_id:
            raise PermissionError("spreadsheet_validation_reference_owner_required")
        return value

    def list_validation_references(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        if self._validation_references is None:
            raise RuntimeError("spreadsheet_validation_reference_store_unavailable")
        page = self._validation_references.list_references(tenant_id, limit=limit)
        return {**page, "items": [item for item in page["items"] if item["owner_id"] == principal_id]}

    def get_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version: int,
        principal_id: str,
    ) -> dict[str, Any]:
        value = self._store.get_version(tenant_id, document_id, version)
        if value["owner_id"] != principal_id:
            raise PermissionError("spreadsheet_document_owner_required")
        return value

    def list_versions(
        self,
        *,
        tenant_id: str,
        document_id: str,
        principal_id: str,
        limit: int = 100,
    ) -> dict[str, Any]:
        current = self.get_document(
            tenant_id=tenant_id,
            document_id=document_id,
            principal_id=principal_id,
        )
        page = self._store.list_versions(tenant_id, document_id, limit=limit)
        if any(item.get("owner_id") != current["owner_id"] for item in page["items"]):
            raise RuntimeError("spreadsheet_document_version_owner_integrity_failed")
        return page

    def list_documents(self, *, tenant_id: str, principal_id: str, limit: int = 100) -> dict[str, Any]:
        page = self._store.list_documents(tenant_id, limit=limit)
        return {**page, "items": [item for item in page["items"] if item["owner_id"] == principal_id]}

    def get_proposal_diff(
        self,
        *,
        tenant_id: str,
        proposal_id: str,
        principal_id: str,
        offset: int = 0,
        limit: int = 1_000,
    ) -> dict[str, Any]:
        result = self._store.get_proposal(tenant_id, require_id(proposal_id, "proposal_id"))
        if result is None:
            raise KeyError("spreadsheet_proposal_not_found")
        current = self.get_document(
            tenant_id=tenant_id,
            document_id=str(result["document_id"]),
            principal_id=principal_id,
        )
        base = self._store.get_version(
            tenant_id,
            str(result["document_id"]),
            int(result["base_version"]),
        )
        if base["owner_id"] != current["owner_id"]:
            raise RuntimeError("spreadsheet_proposal_owner_integrity_failed")
        return self._actual_diff.build(
            before=base["snapshot"],
            after=result["candidate_snapshot"],
            execution_diff=list(result.get("diff") or []),
            actions=list(result.get("actions") or []),
            offset=offset,
            limit=limit,
        )

    def download_original(self, *, tenant_id: str, document_id: str, principal_id: str) -> tuple[bytes, dict[str, Any]]:
        if self._artifacts is None:
            raise RuntimeError("spreadsheet_artifact_store_unavailable")
        document = self.get_document(tenant_id=tenant_id, document_id=document_id, principal_id=principal_id)
        source = document.get("source_artifact")
        if not isinstance(source, Mapping):
            raise KeyError("spreadsheet_original_artifact_not_found")
        content = self._artifacts.read(
            tenant_id=tenant_id,
            sha256=str(source.get("sha256") or ""),
            format=str(source.get("format") or ""),
        )
        return content, dict(source)

    def download_published(
        self, *, tenant_id: str, document_id: str, principal_id: str
    ) -> tuple[bytes, dict[str, Any]]:
        if self._artifacts is None:
            raise RuntimeError("spreadsheet_artifact_store_unavailable")
        document = self.get_document(tenant_id=tenant_id, document_id=document_id, principal_id=principal_id)
        artifact = document.get("published_artifact") or document.get("source_artifact")
        if not isinstance(artifact, Mapping):
            raise KeyError("spreadsheet_published_artifact_not_found")
        content = self._artifacts.read(
            tenant_id=tenant_id,
            sha256=str(artifact.get("sha256") or ""),
            format=str(artifact.get("format") or ""),
        )
        return content, dict(artifact)

    def _store_result_artifact(self, *, tenant_id: str, execution: dict[str, Any]) -> dict[str, Any] | None:
        raw = execution.pop("result_artifact", None)
        if raw is None:
            return None
        if self._artifacts is None or not isinstance(raw, Mapping):
            raise ValueError("spreadsheet_result_artifact_invalid")
        if set(raw) != {"content_base64", "sha256", "size_bytes", "format", "media_type"}:
            raise ValueError("spreadsheet_result_artifact_fields_invalid")
        try:
            content = base64.b64decode(str(raw["content_base64"]), validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("spreadsheet_result_artifact_content_invalid") from exc
        if len(content) != raw["size_bytes"] or hashlib.sha256(content).hexdigest() != raw["sha256"]:
            raise ValueError("spreadsheet_result_artifact_digest_invalid")
        stored = self._artifacts.store(
            tenant_id=tenant_id,
            content=content,
            format=str(raw["format"]),
            media_type=str(raw["media_type"]),
            expected_sha256=str(raw["sha256"]),
        )
        return {
            "artifact_id": stored.artifact_id,
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "format": stored.format,
            "media_type": stored.media_type,
        }


def _candidate_recalc_profile(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "locale": snapshot.get("locale", "und"),
        "timezone": snapshot.get("timezone", "UTC"),
        "date_system": snapshot.get("date_system", "1900"),
        "recalc_profile": snapshot.get("recalc_profile", "automatic"),
    }


__all__ = ["SpreadsheetSagaService"]
