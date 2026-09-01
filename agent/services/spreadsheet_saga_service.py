"""Hub-owned proposal, validation and automatic promotion saga."""

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from collections.abc import Mapping
from typing import Any

from agent.services.spreadsheet_artifact_store import SpreadsheetArtifactStore
from agent.services.spreadsheet_execution_ports import SpreadsheetExecutionPort
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_store import SpreadsheetStore, SpreadsheetStoreConflict
from agent.services.spreadsheet_validator_engine import SpreadsheetValidatorEngine
from ananta_contracts.spreadsheet_studio import (
    SpreadsheetProposalV1,
    WorkbookSnapshotV1,
    require_id,
)


class SpreadsheetSagaService:
    def __init__(
        self,
        store: SpreadsheetStore,
        *,
        policy: SpreadsheetPolicy,
        executor: SpreadsheetExecutionPort,
        artifact_store: SpreadsheetArtifactStore | None = None,
        validator_engine: SpreadsheetValidatorEngine | None = None,
        training_available: bool = False,
    ) -> None:
        policy.validate()
        self._store = store
        self._policy = policy
        self._executor = executor
        self._artifacts = artifact_store
        self._validators = validator_engine or SpreadsheetValidatorEngine()
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
        parsed = WorkbookSnapshotV1.from_mapping(snapshot)
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
        parsed = SpreadsheetProposalV1.from_mapping(proposal)
        existing = self._store.get_proposal(tenant_id, parsed.proposal_id)
        if existing is not None:
            if existing.get("proposal_digest") != parsed.digest:
                raise SpreadsheetStoreConflict("spreadsheet_proposal_replay_conflict")
            return {**existing, "replayed": True}
        document = self._store.get_document(tenant_id, parsed.document_id)
        if document["owner_id"] != principal_id:
            raise PermissionError("spreadsheet_document_owner_required")
        if document["version"] != parsed.expected_version:
            raise SpreadsheetStoreConflict("spreadsheet_document_version_conflict")
        if document["snapshot_digest"] != parsed.base_snapshot_digest:
            raise SpreadsheetStoreConflict("spreadsheet_snapshot_digest_conflict")
        if document.get("unsupported_objects"):
            raise PermissionError("spreadsheet_document_unsupported_semantics")
        snapshot = WorkbookSnapshotV1.from_mapping(document["snapshot"])
        self._policy.admit(snapshot, parsed)
        source_input = None
        source = document.get("source_artifact")
        if isinstance(source, Mapping):
            if self._artifacts is None:
                raise RuntimeError("spreadsheet_artifact_store_unavailable")
            source_input = {
                "content": self._artifacts.read(
                    tenant_id=tenant_id,
                    sha256=str(source.get("sha256") or ""),
                    format=str(source.get("format") or ""),
                ),
                "filename": f"{parsed.document_id}.{source.get('format')}",
                "media_type": str(source.get("media_type") or ""),
                "sha256": str(source.get("sha256") or ""),
            }
        execution = dict(
            self._executor.dry_run(
                snapshot=snapshot.to_dict(),
                actions=parsed.actions,
                **({"source_artifact": source_input} if source_input is not None else {}),
            )
        )
        candidate_artifact = self._store_result_artifact(tenant_id=tenant_id, execution=execution)
        candidate = WorkbookSnapshotV1.from_mapping(execution["candidate_snapshot"])
        if candidate.digest != execution.get("candidate_snapshot_digest"):
            raise ValueError("spreadsheet_execution_digest_invalid")
        validation = self._validators.validate(candidate, parsed.validators)
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

    def get_document(self, *, tenant_id: str, document_id: str, principal_id: str) -> dict[str, Any]:
        value = self._store.get_document(tenant_id, document_id)
        if value["owner_id"] != principal_id:
            raise PermissionError("spreadsheet_document_owner_required")
        return value

    def list_documents(self, *, tenant_id: str, principal_id: str, limit: int = 100) -> dict[str, Any]:
        page = self._store.list_documents(tenant_id, limit=limit)
        return {**page, "items": [item for item in page["items"] if item["owner_id"] == principal_id]}

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


__all__ = ["SpreadsheetSagaService"]
