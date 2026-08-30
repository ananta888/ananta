"""Hub-owned proposal, validation and automatic promotion saga."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from typing import Any

from agent.services.spreadsheet_execution_ports import SpreadsheetExecutionPort
from agent.services.spreadsheet_policy import SpreadsheetPolicy
from agent.services.spreadsheet_store import SpreadsheetStore, SpreadsheetStoreConflict
from ananta_contracts.spreadsheet_studio import (
    SpreadsheetProposalV1,
    WorkbookSnapshotV1,
    canonical_digest,
    require_id,
)


class SpreadsheetSagaService:
    def __init__(
        self,
        store: SpreadsheetStore,
        *,
        policy: SpreadsheetPolicy,
        executor: SpreadsheetExecutionPort,
    ) -> None:
        policy.validate()
        self._store = store
        self._policy = policy
        self._executor = executor

    def capabilities(self) -> dict[str, Any]:
        capability = dict(self._executor.capability)
        available = self._policy.enabled and capability.get("state") == "available"
        return {
            "schema": "ananta.spreadsheet-studio-capability.v1",
            "available": available,
            "state": "available" if available else "disabled",
            "mode": self._policy.mode,
            "automatic_promotion_enabled": self._policy.automatic_promotion_enabled,
            "executor": capability,
            "supported_formats": ["canonical_snapshot"],
            "libreoffice_fidelity_verified": False,
            "training_available": False,
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
        snapshot = WorkbookSnapshotV1.from_mapping(document["snapshot"])
        self._policy.admit(snapshot, parsed)
        execution = dict(self._executor.dry_run(snapshot=snapshot.to_dict(), actions=parsed.actions))
        candidate = WorkbookSnapshotV1.from_mapping(execution["candidate_snapshot"])
        if candidate.digest != execution.get("candidate_snapshot_digest"):
            raise ValueError("spreadsheet_execution_digest_invalid")
        validation = self._validate(candidate, parsed.validators)
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
            "candidate_snapshot": candidate.to_dict(),
            "candidate_snapshot_digest": candidate.digest,
            "diff": execution["diff"],
            "validation": validation,
            "state": state,
            "reason_codes": reasons,
            "automatic_decision": True,
            "production_fidelity": False,
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

    @staticmethod
    def _validate(snapshot: WorkbookSnapshotV1, validators: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
        sheets = {
            str(sheet["sheet_id"]): {cell["address"]: cell for cell in sheet["cells"]} for sheet in snapshot.sheets
        }
        results: list[dict[str, Any]] = []
        for validator in validators:
            cell = sheets.get(str(validator["sheet_id"]), {}).get(str(validator["cell"]))
            kind = validator["kind"]
            passed = False
            if kind == "equals":
                passed = cell is not None and cell["value"] == validator["expected"]
            elif kind == "number_range":
                value = cell.get("value") if cell else None
                passed = (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and validator["minimum"] <= float(value) <= validator["maximum"]
                )
            elif kind == "formula_present":
                passed = cell is not None and cell["formula"] is not None
            elif kind == "cell_empty":
                passed = cell is None or (cell["value"] is None and cell["formula"] is None)
            results.append(
                {
                    "validator_id": validator["validator_id"],
                    "passed": passed,
                    "reason_code": None if passed else "spreadsheet_validator_failed",
                }
            )
        reasons = [item["reason_code"] for item in results if item["reason_code"]]
        return {
            "schema": "ananta.spreadsheet-validation-result.v1",
            "passed": not reasons,
            "results": results,
            "reason_codes": sorted(set(reasons)),
            "validation_digest": canonical_digest(results),
            "human_intervention_required": False,
        }


__all__ = ["SpreadsheetSagaService"]
