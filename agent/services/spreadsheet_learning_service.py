"""Hub-owned feedback, consent and dataset lifecycle for Spreadsheet Studio."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.services.spreadsheet_dataset_split_service import SpreadsheetDatasetSplitService
from agent.services.spreadsheet_learning_repository_port import SpreadsheetLearningRepository
from agent.services.spreadsheet_repository_port import SpreadsheetDocumentRepositoryPort
from ananta_contracts.spreadsheet_studio import (
    SpreadsheetProposalV1,
    canonical_digest,
    canonical_json,
    require_digest,
    require_id,
)

_FEEDBACK_KINDS = frozenset({"accepted", "corrected", "rejected", "skipped", "unsafe"})
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_SECRET = re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*[^\s,;]{4,}|\bsk-[A-Za-z0-9_-]{12,}")


class SpreadsheetLearningService:
    PROJECTOR_VERSION = "spreadsheet-training-example.v1"
    MASKING_VERSION = "spreadsheet-masking.v1"
    SERIALIZER_VERSION = "spreadsheet-action-json.v1"
    POLICY_VERSION = "spreadsheet-learning-policy.v1"

    def __init__(
        self,
        *,
        documents: SpreadsheetDocumentRepositoryPort,
        store: SpreadsheetLearningRepository,
        dataset_root: str | Path,
        split_service: SpreadsheetDatasetSplitService | None = None,
        clock=time.time,
    ) -> None:
        self._documents = documents
        self._store = store
        self._dataset_root = Path(dataset_root)
        self._split_service = split_service or SpreadsheetDatasetSplitService()
        self._clock = clock

    def record_feedback(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {
            "schema",
            "event_id",
            "document_id",
            "proposal_id",
            "kind",
            "instruction",
            "correction_actions",
            "excluded_cells",
        }
        if set(payload) != required or payload.get("schema") != "ananta.spreadsheet-feedback-command.v1":
            raise ValueError("spreadsheet_feedback_fields_invalid")
        event_id = require_id(payload.get("event_id"), "feedback_event_id")
        document_id = require_id(payload.get("document_id"), "document_id")
        proposal_id = require_id(payload.get("proposal_id"), "proposal_id")
        kind = str(payload.get("kind") or "")
        if kind not in _FEEDBACK_KINDS:
            raise ValueError("spreadsheet_feedback_kind_invalid")
        instruction = str(payload.get("instruction") or "").strip()
        if not 1 <= len(instruction) <= 4_000:
            raise ValueError("spreadsheet_feedback_instruction_invalid")
        document = self._documents.get_document(tenant_id, document_id)
        if document.get("owner_id") != principal_id:
            raise PermissionError("spreadsheet_document_owner_required")
        proposal = self._documents.get_proposal(tenant_id, proposal_id)
        if proposal is None or proposal.get("document_id") != document_id:
            raise KeyError("spreadsheet_proposal_not_found")
        correction_actions = self._validated_actions(payload.get("correction_actions"), document, proposal_id)
        if kind == "corrected" and not correction_actions:
            raise ValueError("spreadsheet_feedback_correction_required")
        if kind != "corrected" and correction_actions:
            raise ValueError("spreadsheet_feedback_correction_forbidden")
        excluded = self._excluded_cells(payload.get("excluded_cells"))
        target_actions = correction_actions or tuple(dict(action) for action in proposal.get("actions") or [])
        record = self._project_record(
            document=document,
            proposal=proposal,
            instruction=instruction,
            target_actions=target_actions,
            excluded_cells=excluded,
            kind=kind,
        )
        event = {
            "schema": "ananta.spreadsheet-feedback-event.v1",
            "event_id": event_id,
            "tenant_id": tenant_id,
            "owner_id": principal_id,
            "document_id": document_id,
            "document_version": int(document["version"]),
            "proposal_id": proposal_id,
            "proposal_digest": proposal["proposal_digest"],
            "candidate_snapshot_digest": proposal["candidate_snapshot_digest"],
            "kind": kind,
            "record": record,
            "record_digest": canonical_digest(record),
            "created_at": float(self._clock()),
            "human_intervention_required": False,
            "policy_decision": {
                "purpose": "spreadsheet_action_training",
                "privacy": "masked_before_consent",
                "tenant_pooling": "forbidden",
                "license_state": "source_owner_submission",
                "minimization": "bounded_proposal_diff_and_actions",
                "reason_codes": ["spreadsheet_training_record_masked"],
            },
        }
        event["digest"] = canonical_digest(event)
        return self._store.append_feedback(tenant_id, event)

    def privacy_preview(self, *, tenant_id: str, principal_id: str, event_id: str) -> dict[str, Any]:
        event = self._owned_feedback(tenant_id, principal_id, event_id)
        return {
            "schema": "ananta.spreadsheet-training-privacy-preview.v1",
            "event_id": event["event_id"],
            "record": event["record"],
            "record_digest": event["record_digest"],
            "purpose": "spreadsheet_action_training",
            "projector_version": self.PROJECTOR_VERSION,
            "masking_version": self.MASKING_VERSION,
            "serializer_version": self.SERIALIZER_VERSION,
            "policy_version": self.POLICY_VERSION,
            "human_intervention_required": False,
        }

    def grant_consent(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {"schema", "consent_id", "feedback_id", "record_digest", "purpose", "retention_days", "granted"}
        if set(payload) != required or payload.get("schema") != "ananta.spreadsheet-training-consent-command.v1":
            raise ValueError("spreadsheet_consent_fields_invalid")
        if payload.get("granted") is not True or payload.get("purpose") != "spreadsheet_action_training":
            raise PermissionError("spreadsheet_training_consent_missing")
        event = self._owned_feedback(tenant_id, principal_id, str(payload.get("feedback_id") or ""))
        if require_digest(payload.get("record_digest"), "record_digest") != event["record_digest"]:
            raise ValueError("spreadsheet_consent_record_digest_mismatch")
        retention_days = payload.get("retention_days")
        if not isinstance(retention_days, int) or isinstance(retention_days, bool) or not 1 <= retention_days <= 3_650:
            raise ValueError("spreadsheet_consent_retention_invalid")
        consent = {
            "schema": "ananta.spreadsheet-training-consent.v1",
            "consent_id": require_id(payload.get("consent_id"), "consent_id"),
            "feedback_id": event["event_id"],
            "owner_id": principal_id,
            "record_digest": event["record_digest"],
            "source_document_id": event["document_id"],
            "source_document_version": event["document_version"],
            "purpose": payload["purpose"],
            "projector_version": self.PROJECTOR_VERSION,
            "masking_version": self.MASKING_VERSION,
            "serializer_version": self.SERIALIZER_VERSION,
            "policy_version": self.POLICY_VERSION,
            "version": 1,
            "state": "active",
            "granted_at": float(self._clock()),
            "expires_at": float(self._clock()) + retention_days * 86_400,
            "revocation_epoch": 0,
            "human_intervention_required": False,
        }
        consent["consent_digest"] = canonical_digest(consent)
        return self._store.append_consent(tenant_id, consent)

    def revoke_consent(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        consent_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        current = self._store.get_consent(tenant_id, consent_id)
        if current.get("owner_id") != principal_id:
            raise PermissionError("spreadsheet_consent_owner_required")
        if current.get("state") != "active" or current.get("version") != expected_version:
            raise PermissionError("spreadsheet_consent_stale_or_inactive")
        revoked = {
            **current,
            "version": expected_version + 1,
            "state": "revoked",
            "revoked_at": float(self._clock()),
            "revocation_epoch": int(current["revocation_epoch"]) + 1,
        }
        revoked.pop("consent_digest", None)
        revoked["consent_digest"] = canonical_digest(revoked)
        datasets = [
            dataset for dataset in self._store.list_datasets(tenant_id) if self._dataset_uses_consent(dataset, current)
        ]
        dataset_ids = sorted(str(dataset["dataset_id"]) for dataset in datasets)
        jobs = [
            lineage
            for lineage in self._store.list_training_lineage(tenant_id)
            if str(lineage.get("dataset_id") or "") in dataset_ids
        ]
        impact = {
            "schema": "ananta.spreadsheet-consent-revocation-impact.v1",
            "impact_id": f"revocation-{current['consent_id']}-{revoked['revocation_epoch']}",
            "consent_id": current["consent_id"],
            "consent_digest": current["consent_digest"],
            "revocation_epoch": revoked["revocation_epoch"],
            "dataset_ids": dataset_ids,
            "training_jobs": [
                {
                    "job_id": str(lineage["job_id"]),
                    "owner_id": str(lineage["owner_id"]),
                    "state": "fence_required",
                }
                for lineage in jobs
            ],
            "adapter_ids": sorted(
                {
                    str(adapter_id)
                    for lineage in jobs
                    for adapter_id in list(lineage.get("adapter_ids") or [])
                    if str(adapter_id)
                }
            ),
            "evaluation_ids": sorted(
                {
                    str(evaluation_id)
                    for lineage in jobs
                    for evaluation_id in list(lineage.get("evaluation_ids") or [])
                    if str(evaluation_id)
                }
            ),
            "adapter_action": "quarantine_deprecate_and_retrain",
            "mathematical_unlearning_claimed": False,
            "state": "fence_required" if jobs else "quarantined",
            "reason_code": "spreadsheet_consent_revoked",
            "created_at": float(self._clock()),
            "human_intervention_required": False,
        }
        impact["digest"] = canonical_digest(impact)
        persisted, persisted_impact = self._store.append_consent_with_impact(tenant_id, revoked, impact)
        return {**persisted, "impact": persisted_impact}

    def materialize_dataset(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        required = {"schema", "dataset_id", "feedback_ids", "recipe_version", "split_seed", "split_percent"}
        if set(payload) != required or payload.get("schema") != "ananta.spreadsheet-dataset-command.v1":
            raise ValueError("spreadsheet_dataset_fields_invalid")
        feedback_ids = payload.get("feedback_ids")
        if (
            not isinstance(feedback_ids, Sequence)
            or isinstance(feedback_ids, (str, bytes))
            or not 1 <= len(feedback_ids) <= 100_000
        ):
            raise ValueError("spreadsheet_dataset_feedback_ids_invalid")
        if len(set(str(value) for value in feedback_ids)) != len(feedback_ids):
            raise ValueError("spreadsheet_dataset_feedback_ids_duplicate")
        split = self._split_percent(payload.get("split_percent"))
        seed = require_id(payload.get("split_seed"), "split_seed")
        recipe_version = require_id(payload.get("recipe_version"), "recipe_version")
        candidate_rows: list[dict[str, Any]] = []
        consent_by_feedback: dict[str, dict[str, Any]] = {}
        now = float(self._clock())
        for raw_id in feedback_ids:
            event = self._owned_feedback(tenant_id, principal_id, str(raw_id))
            if event["kind"] not in {"accepted", "corrected"}:
                raise PermissionError("spreadsheet_feedback_not_training_eligible")
            consent = self._store.get_active_consent_for_feedback(tenant_id, event["event_id"])
            if consent.get("owner_id") != principal_id or consent.get("record_digest") != event["record_digest"]:
                raise PermissionError("spreadsheet_consent_binding_invalid")
            if float(consent.get("expires_at") or 0) <= now:
                raise PermissionError("spreadsheet_consent_expired")
            lineage = str(event["document_id"])
            candidate_rows.append(
                {
                    **event["record"],
                    "record_digest": event["record_digest"],
                    "feedback_id": event["event_id"],
                    "consent_id": consent["consent_id"],
                    "consent_digest": consent["consent_digest"],
                    "lineage_root_id": lineage,
                    "recipe_version": recipe_version,
                }
            )
            consent_by_feedback[event["event_id"]] = {
                "consent_id": consent["consent_id"],
                "consent_digest": consent["consent_digest"],
                "consent_version": consent["version"],
                "revocation_epoch": consent["revocation_epoch"],
            }
        preparation = self._split_service.prepare(candidate_rows, seed=seed, split_percent=split)
        rows = list(preparation.rows)
        used_feedback_ids = {str(row["feedback_id"]) for row in rows}
        consent_refs = [consent_by_feedback[feedback_id] for feedback_id in sorted(used_feedback_ids)]
        consent_digests = [str(reference["consent_digest"]) for reference in consent_refs]
        content = "".join(canonical_json(row) + "\n" for row in rows).encode()
        dataset_digest = hashlib.sha256(content).hexdigest()
        self._write_dataset(tenant_id=tenant_id, digest=dataset_digest, content=content)
        counts = {name: sum(row["split"] == name for row in rows) for name in ("train", "validation", "eval", "test")}
        recipe_manifest = {
            "schema": "ananta.spreadsheet-dataset-recipe-manifest.v1",
            "recipe_version": recipe_version,
            "source_feedback_digests": sorted(str(row["record_digest"]) for row in rows),
            "task_kinds": ["spreadsheet_actions"],
            "action_schema": "ananta.spreadsheet-action-output.v1",
            "projector_version": self.PROJECTOR_VERSION,
            "masking_version": self.MASKING_VERSION,
            "serializer_version": self.SERIALIZER_VERSION,
            "policy_version": self.POLICY_VERSION,
            "purpose": "spreadsheet_action_training",
            "tenant_pooling": "forbidden",
            "split_lock_digest": preparation.split_lock["split_lock_digest"],
            "retention_binding": "consent_expiry",
        }
        recipe_manifest["recipe_digest"] = canonical_digest(recipe_manifest)
        dataset = {
            "schema": "ananta.spreadsheet-dataset.v1",
            "dataset_id": require_id(payload.get("dataset_id"), "dataset_id"),
            "owner_id": principal_id,
            "dataset_digest": dataset_digest,
            "artifact_id": f"spreadsheet-dataset-{dataset_digest[:32]}",
            "record_count": len(rows),
            "split_counts": counts,
            "split_seed": seed,
            "split_percent": split,
            "split_lock": preparation.split_lock,
            "recipe_version": recipe_version,
            "recipe_manifest": recipe_manifest,
            "projector_version": self.PROJECTOR_VERSION,
            "masking_version": self.MASKING_VERSION,
            "serializer_version": self.SERIALIZER_VERSION,
            "policy_version": self.POLICY_VERSION,
            "consent_digests": sorted(consent_digests),
            "consent_refs": sorted(consent_refs, key=lambda item: (item["consent_id"], item["consent_digest"])),
            "materialization": {
                "mode": "offline_artifact",
                "artifact_digest": dataset_digest,
                "attempt_binding": f"materialization-{dataset_digest[:32]}",
                "state": "materialized",
                "excluded_feedback_ids": list(preparation.excluded_feedback_ids),
            },
            "readiness": {
                "dry_run_ready": bool(rows),
                "training_ready": len(rows) >= 100 and counts["train"] > 0 and counts["validation"] > 0,
                "reason_codes": sorted(
                    ([] if len(rows) >= 100 else ["spreadsheet_dataset_minimum_records_not_met"])
                    + list(preparation.split_lock["distribution_warnings"])
                ),
            },
            "created_at": now,
            "human_intervention_required": False,
        }
        dataset["digest"] = canonical_digest(dataset)
        return self._store.append_dataset(tenant_id, dataset)

    def get_dataset(self, *, tenant_id: str, principal_id: str, dataset_id: str) -> dict[str, Any]:
        value = self._store.get_dataset(tenant_id, dataset_id)
        if value.get("owner_id") != principal_id:
            raise PermissionError("spreadsheet_dataset_owner_required")
        stale = []
        for reference in list(value.get("consent_refs") or []):
            try:
                current = self._store.get_consent(tenant_id, str(reference.get("consent_id") or ""))
            except KeyError:
                stale.append(str(reference.get("consent_id") or ""))
                continue
            if (
                current.get("state") != "active"
                or current.get("consent_digest") != reference.get("consent_digest")
                or current.get("version") != reference.get("consent_version")
                or current.get("revocation_epoch") != reference.get("revocation_epoch")
                or float(current.get("expires_at") or 0) <= float(self._clock())
            ):
                stale.append(str(reference.get("consent_id") or ""))
        if not stale:
            return value
        return {
            **value,
            "state": "quarantined",
            "readiness": {
                "dry_run_ready": False,
                "training_ready": False,
                "reason_codes": ["spreadsheet_dataset_consent_stale"],
            },
            "revocation_impact": {
                "consent_ids": sorted(stale),
                "adapter_action": "quarantine_or_retrain",
                "mathematical_unlearning_claimed": False,
            },
        }

    def record_training_lineage(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        dataset_id: str,
        ml_intern_dataset_id: str,
        job: Mapping[str, Any],
    ) -> dict[str, Any]:
        dataset = self.get_dataset(tenant_id=tenant_id, principal_id=principal_id, dataset_id=dataset_id)
        job_id = require_id(job.get("id") or job.get("job_id"), "training_job_id")
        value = {
            "schema": "ananta.spreadsheet-training-lineage.v1",
            "job_id": job_id,
            "dataset_id": dataset["dataset_id"],
            "dataset_digest": dataset["dataset_digest"],
            "ml_intern_dataset_id": require_id(ml_intern_dataset_id, "ml_intern_dataset_id"),
            "owner_id": principal_id,
            "consent_refs": list(dataset.get("consent_refs") or []),
            "adapter_ids": self._lineage_ids(job, "adapter_ids", "adapter_id"),
            "evaluation_ids": self._lineage_ids(job, "evaluation_ids", "evaluation_id"),
            "created_at": float(self._clock()),
            "human_intervention_required": False,
        }
        value["digest"] = canonical_digest(value)
        return self._store.append_training_lineage(tenant_id, value)

    @staticmethod
    def _lineage_ids(job: Mapping[str, Any], plural: str, singular: str) -> list[str]:
        raw = job.get(plural)
        values = list(raw) if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else []
        if job.get(singular):
            values.append(job[singular])
        return sorted({str(value) for value in values if str(value)})

    @staticmethod
    def _dataset_uses_consent(dataset: Mapping[str, Any], consent: Mapping[str, Any]) -> bool:
        refs = list(dataset.get("consent_refs") or [])
        if refs:
            return any(
                reference.get("consent_id") == consent.get("consent_id")
                and reference.get("consent_digest") == consent.get("consent_digest")
                for reference in refs
            )
        return consent.get("consent_digest") in set(dataset.get("consent_digests") or [])

    def dataset_path(self, *, tenant_id: str, principal_id: str, dataset_id: str) -> Path:
        dataset = self.get_dataset(tenant_id=tenant_id, principal_id=principal_id, dataset_id=dataset_id)
        return self._dataset_path(tenant_id, str(dataset["dataset_digest"]))

    def _owned_feedback(self, tenant_id: str, principal_id: str, event_id: str) -> dict[str, Any]:
        event = self._store.get_feedback(tenant_id, event_id)
        if event.get("owner_id") != principal_id:
            raise PermissionError("spreadsheet_feedback_owner_required")
        return event

    @staticmethod
    def _validated_actions(value: Any, document: Mapping[str, Any], proposal_id: str) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
            raise ValueError("spreadsheet_feedback_actions_invalid")
        if not value:
            return ()
        parsed = SpreadsheetProposalV1.from_mapping(
            {
                "schema": SpreadsheetProposalV1.SCHEMA,
                "proposal_id": f"correction-{proposal_id}",
                "document_id": document["document_id"],
                "expected_version": document["version"],
                "base_snapshot_digest": document["snapshot_digest"],
                "actions": list(value),
                "validators": [],
                "automatic_promotion": False,
            }
        )
        return parsed.actions

    @staticmethod
    def _excluded_cells(value: Any) -> frozenset[tuple[str, str]]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > 10_000:
            raise ValueError("spreadsheet_feedback_excluded_cells_invalid")
        result = set()
        for item in value:
            if not isinstance(item, Mapping) or set(item) != {"sheet_id", "cell"}:
                raise ValueError("spreadsheet_feedback_excluded_cells_invalid")
            result.add((require_id(item.get("sheet_id"), "sheet_id"), str(item.get("cell") or "").upper()))
        return frozenset(result)

    def _project_record(
        self,
        *,
        document: Mapping[str, Any],
        proposal: Mapping[str, Any],
        instruction: str,
        target_actions: Sequence[Mapping[str, Any]],
        excluded_cells: frozenset[tuple[str, str]],
        kind: str,
    ) -> dict[str, Any]:
        context = []
        for item in list(proposal.get("diff") or []):
            key = (str(item.get("sheet_id")), str(item.get("cell")))
            if key in excluded_cells:
                continue
            context.append(
                {
                    "sheet_id": key[0],
                    "cell": key[1],
                    "before": self._mask(item.get("before")),
                    "after": self._mask(item.get("after")),
                    "direct": bool(item.get("direct")),
                }
            )
        projected_actions = [
            self._mask(dict(action))
            for action in target_actions
            if (str(action["sheet_id"]), str(action["cell"])) not in excluded_cells
        ]
        return {
            "instruction": self._mask(instruction),
            "input": canonical_json(
                {
                    "schema": "ananta.spreadsheet-training-context.v1",
                    "base_snapshot_digest": proposal["base_snapshot_digest"],
                    "cells": context,
                }
            ),
            "output": canonical_json(
                {
                    "schema": "ananta.spreadsheet-action-output.v1",
                    "actions": projected_actions,
                }
            ),
            "task_kind": "spreadsheet_actions",
            "privacy_class": "consented_masked",
            "quality_label": kind,
            "source_document_version": int(document["version"]),
        }

    def _mask(self, value: Any) -> Any:
        if isinstance(value, str):
            return _SECRET.sub("<SECRET>", _EMAIL.sub("<EMAIL>", value))
        if isinstance(value, Mapping):
            return {str(key): self._mask(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self._mask(child) for child in value]
        return value

    @staticmethod
    def _split_percent(value: Any) -> dict[str, int]:
        if not isinstance(value, Mapping) or set(value) != {"train", "validation", "eval", "test"}:
            raise ValueError("spreadsheet_dataset_split_invalid")
        split = {key: int(value[key]) for key in ("train", "validation", "eval", "test")}
        if (
            any(isinstance(value[key], bool) or not 0 <= split[key] <= 100 for key in split)
            or sum(split.values()) != 100
        ):
            raise ValueError("spreadsheet_dataset_split_invalid")
        return split

    def _write_dataset(self, *, tenant_id: str, digest: str, content: bytes) -> None:
        target = self._dataset_path(tenant_id, digest)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("spreadsheet_dataset_artifact_collision")
            return
        descriptor, temporary = tempfile.mkstemp(prefix=".dataset-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _dataset_path(self, tenant_id: str, digest: str) -> Path:
        tenant_hash = hashlib.sha256(require_id(tenant_id, "tenant_id").encode()).hexdigest()[:32]
        return self._dataset_root / tenant_hash / require_digest(digest, "dataset_digest") / "dataset.jsonl"


__all__ = ["SpreadsheetLearningService"]
