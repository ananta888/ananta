"""Content-free persistent read models for Base-vs-Adapter evaluations."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.services.ml_intern_artifact_security_service import MlInternArtifactSecurityService
from agent.services.ml_intern_evaluation_decision_service import (
    EvaluationDecision,
    evaluate_adapter_metrics,
)
from agent.services.ml_intern_training_repository_port import MlInternTrainingPrincipal
from agent.services.unsloth_storage_contracts import StorageReferencePort


class EvaluationStoreError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class MlInternEvaluationStoreService:
    """Persist tenant-bound, bounded Base-vs-Adapter read models."""

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        storage_references: StorageReferencePort | None = None,
    ) -> None:
        self._security = MlInternArtifactSecurityService(storage_root=artifact_root)
        self._storage_references = storage_references

    def save(
        self,
        principal: MlInternTrainingPrincipal,
        *,
        adapter_id: str,
        dataset_id: str,
        metrics: Mapping[str, Any],
        samples: Sequence[Any] | None = None,
        status: str = "completed",
        reason_code: str | None = None,
        evaluation_id: str | None = None,
        minimum_score: float = 0.0,
        decision: EvaluationDecision | None = None,
        promotion_evidence: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_metrics = _metric_rows(metrics)
        canonical_decision = decision or evaluate_adapter_metrics(metrics, minimum_score=minimum_score)
        now = time.time()
        identifier = evaluation_id or f"lora-eval-{uuid.uuid4()}"
        payload = {
            "schema": "ananta.ml-intern-evaluation.v1",
            "id": identifier,
            "tenant_digest": hashlib.sha256(principal.tenant_id.encode()).hexdigest(),
            "owner_digest": hashlib.sha256(principal.subject.encode()).hexdigest(),
            "adapter_id": adapter_id,
            "dataset_id": dataset_id,
            "status": status,
            "passed": canonical_decision.passed if status == "completed" else None,
            "aggregate_score": canonical_decision.score,
            "metrics": safe_metrics,
            "samples": _sample_rows(samples if samples is not None else metrics.get("samples")),
            "reason_code": reason_code or canonical_decision.reason_code,
            "created_at": now,
            "finished_at": now if status in {"completed", "failed", "cancelled"} else None,
            "promotion_evidence": _promotion_evidence(promotion_evidence),
        }
        tenant_key = payload["tenant_digest"]
        owner_key = payload["owner_digest"]
        self._security.atomic_write_json(f"evaluations/{tenant_key}/{owner_key}/{identifier}.json", payload)
        evidence = payload.get("promotion_evidence")
        if self._storage_references is not None and isinstance(evidence, Mapping):
            try:
                self._storage_references.bind_reference(
                    tenant_id=principal.tenant_id,
                    reference_kind="evaluation",
                    reference_id=identifier,
                    artifact_id=str(evidence["adapter_id"]),
                    artifact_sha256=str(evidence["artifact_sha256"]),
                )
            except Exception as exc:
                raise EvaluationStoreError(
                    "evaluation_storage_binding_failed",
                    "evaluation artifact reference could not be bound",
                ) from exc
        return _public(payload)

    def get(
        self,
        principal: MlInternTrainingPrincipal,
        evaluation_id: str,
    ) -> dict[str, Any]:
        return _public(self._load(principal, evaluation_id))

    def get_promotion_evidence(
        self,
        principal: MlInternTrainingPrincipal,
        evaluation_id: str,
    ) -> dict[str, Any]:
        payload = self._load(principal, evaluation_id)
        evidence = payload.get("promotion_evidence")
        if not isinstance(evidence, Mapping):
            raise EvaluationStoreError(
                "evaluation_promotion_evidence_missing",
                "evaluation has no verified promotion evidence",
            )
        return dict(evidence)

    def _load(
        self,
        principal: MlInternTrainingPrincipal,
        evaluation_id: str,
    ) -> dict[str, Any]:
        tenant_key = hashlib.sha256(principal.tenant_id.encode()).hexdigest()
        owner_key = hashlib.sha256(principal.subject.encode()).hexdigest()
        try:
            path = self._security.resolve_relative(
                f"evaluations/{tenant_key}/{owner_key}/{evaluation_id}.json",
                must_exist=True,
            )
        except Exception as exc:
            raise EvaluationStoreError("evaluation_not_found", "evaluation does not exist") from exc
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise EvaluationStoreError("evaluation_corrupt", "evaluation read model is corrupt") from exc
        if not isinstance(payload, dict) or payload.get("schema") != "ananta.ml-intern-evaluation.v1":
            raise EvaluationStoreError("evaluation_corrupt", "evaluation read model has an invalid schema")
        return payload


def _metric_rows(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = set()
    base = metrics.get("base") if isinstance(metrics.get("base"), Mapping) else {}
    adapter = metrics.get("adapter") if isinstance(metrics.get("adapter"), Mapping) else {}
    names.update(str(key) for key in base)
    names.update(str(key) for key in adapter)
    for name in sorted(names)[:64]:
        base_value = _finite(base.get(name))
        adapter_value = _finite(adapter.get(name))
        if base_value is None or adapter_value is None:
            continue
        higher_is_better = "loss" not in name.lower() and "error" not in name.lower()
        delta = adapter_value - base_value
        passed = delta >= 0 if higher_is_better else delta <= 0
        rows.append(
            {
                "name": name[:64],
                "base_value": base_value,
                "adapter_value": adapter_value,
                "delta": delta,
                "higher_is_better": higher_is_better,
                "passed": passed,
            }
        )
    return rows


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _sample_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for raw in value[:20]:
        if not isinstance(raw, Mapping):
            continue
        reference = str(raw.get("id") or "").strip().lower()
        if len(reference) != 64 or any(character not in "0123456789abcdef" for character in reference):
            continue
        winner = str(raw.get("winner") or "tie").strip().lower()
        if winner not in {"base", "adapter", "tie"}:
            winner = "tie"
        result.append(
            {
                "prompt_ref": reference,
                "record_index": max(0, min(int(raw.get("record_index") or 0), 1_000_000)),
                "base_output": str(raw.get("base_output") or "")[:2_000],
                "adapter_output": str(raw.get("adapter_output") or "")[:2_000],
                "expected_output": str(raw.get("expected_output") or "")[:2_000] or None,
                "base_score": _safe_score(raw.get("base_score")),
                "adapter_score": _safe_score(raw.get("adapter_score")),
                "winner": winner,
            }
        )
    return result


def _safe_score(value: Any) -> Any:
    finite = _finite(value)
    if finite is not None:
        return finite
    if isinstance(value, Mapping):
        return {
            str(key)[:64]: child
            for key, child in list(value.items())[:32]
            if isinstance(child, (bool, int, float, str)) and len(str(child)) <= 128
        }
    return None


def _public(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "id",
            "adapter_id",
            "dataset_id",
            "status",
            "passed",
            "aggregate_score",
            "metrics",
            "samples",
            "reason_code",
            "created_at",
            "finished_at",
        )
    }


def _promotion_evidence(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    allowed = {
        "job_id",
        "attempt_id",
        "fencing_token_digest",
        "dataset_hash",
        "validation_dataset_hash",
        "base_model_id",
        "base_model_sha256",
        "adapter_id",
        "adapter_sha256",
        "artifact_sha256",
        "export_sha256",
        "source_ids",
        "run_ids",
    }
    if not isinstance(value, Mapping) or set(value) != allowed:
        raise EvaluationStoreError(
            "evaluation_promotion_evidence_invalid",
            "promotion evidence has an invalid contract",
        )
    result = dict(value)
    try:
        encoded = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EvaluationStoreError(
            "evaluation_promotion_evidence_invalid",
            "promotion evidence is not canonical JSON",
        ) from exc
    if len(encoded.encode("utf-8")) > 32 * 1024:
        raise EvaluationStoreError(
            "evaluation_promotion_evidence_invalid",
            "promotion evidence exceeds its bound",
        )
    return json.loads(encoded)
