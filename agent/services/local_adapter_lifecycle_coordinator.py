"""Persistent Hub coordination for local adapter promotion and rollback."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Mapping, Protocol

from agent.services.interprocess_file_transaction import InterProcessFileTransaction
from agent.services.local_adapter_evaluation_service import (
    LocalAdapterEvaluationReport,
)
from agent.services.local_adapter_lifecycle import (
    AdapterGateEvidence,
    LiveAdapterSignals,
    LocalAdapterPromotionPolicy,
    LocalAdapterReleasePolicy,
    LocalAdapterRollbackPolicy,
)
from agent.services.local_adapter_rollout_service import (
    CanaryEvidence,
    ShadowEvidence,
)


class LocalAdapterRegistryPort(Protocol):
    def promote(
        self,
        *,
        candidate_id: str,
        expected_revision: int,
        idempotency_key: str,
        evidence_sha256: str,
    ) -> Mapping[str, object]: ...

    def rollback(self, *, candidate_id: str, reason_code: str) -> Mapping[str, object]: ...


class LocalAdapterRuntimeRestartPort(Protocol):
    def restart(
        self,
        *,
        target: str,
        candidate_id: str | None,
        candidate_sha256: str | None,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class LocalAdapterReleaseBundle:
    candidate_id: str
    target: str
    dataset_sha256: str
    candidate_sha256: str
    evaluation_sha256: str
    shadow_sha256: str
    canary_sha256: str
    policy_sha256: str

    def __post_init__(self) -> None:
        if self.target not in {"needle2", "lfm2.5-2.6b-agentic"}:
            raise ValueError("local_adapter_target_invalid")
        for value in (
            self.dataset_sha256,
            self.candidate_sha256,
            self.evaluation_sha256,
            self.shadow_sha256,
            self.canary_sha256,
            self.policy_sha256,
        ):
            _digest(value)

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LocalAdapterReleaseReceipt:
    candidate_id: str
    target: str
    bundle_sha256: str
    registry_revision: int
    status: str
    completed_at: str


class LocalAdapterLifecycleRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._release_transaction = InterProcessFileTransaction(self._path.with_name(f"{self._path.name}.release.lock"))
        self._initialize()

    def release_transaction(self) -> InterProcessFileTransaction:
        """Serialize registry mutation, runtime restart, and receipt persistence."""

        return self._release_transaction

    def stage(
        self,
        bundle: LocalAdapterReleaseBundle,
        evidence: AdapterGateEvidence,
        evaluation: LocalAdapterEvaluationReport,
        shadow: ShadowEvidence,
        canary: CanaryEvidence,
        policy: LocalAdapterReleasePolicy,
    ) -> None:
        payload = json.dumps(
            {
                "bundle": asdict(bundle),
                "evidence": asdict(evidence),
                "evaluation": asdict(evaluation),
                "shadow": asdict(shadow),
                "canary": asdict(canary),
                "policy": asdict(policy),
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT payload_json FROM staged_releases WHERE candidate_id = ?", (bundle.candidate_id,)
            ).fetchone()
            if existing is not None and existing[0] != payload:
                raise ValueError("local_adapter_stage_immutable_conflict")
            connection.execute(
                "INSERT OR IGNORE INTO staged_releases(candidate_id, payload_json) VALUES (?, ?)",
                (bundle.candidate_id, payload),
            )

    def staged(
        self, candidate_id: str
    ) -> (
        tuple[
            LocalAdapterReleaseBundle,
            AdapterGateEvidence,
            LocalAdapterEvaluationReport,
            ShadowEvidence,
            CanaryEvidence,
            LocalAdapterReleasePolicy,
        ]
        | None
    ):
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM staged_releases WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        return (
            LocalAdapterReleaseBundle(**payload["bundle"]),
            AdapterGateEvidence(**payload["evidence"]),
            LocalAdapterEvaluationReport(**payload["evaluation"]),
            ShadowEvidence(**payload["shadow"]),
            CanaryEvidence(**payload["canary"]),
            LocalAdapterReleasePolicy(**payload["policy"]),
        )

    def receipt(self, idempotency_key: str) -> LocalAdapterReleaseReceipt | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT receipt_json FROM release_receipts WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        return LocalAdapterReleaseReceipt(**json.loads(row[0])) if row else None

    def save_receipt(self, idempotency_key: str, receipt: LocalAdapterReleaseReceipt) -> None:
        payload = json.dumps(asdict(receipt), sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT receipt_json FROM release_receipts WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None and existing[0] != payload:
                raise ValueError("local_adapter_release_idempotency_conflict")
            connection.execute(
                "INSERT OR IGNORE INTO release_receipts(idempotency_key, receipt_json) VALUES (?, ?)",
                (idempotency_key, payload),
            )

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS staged_releases(candidate_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS release_receipts("
                "idempotency_key TEXT PRIMARY KEY, receipt_json TEXT NOT NULL)"
            )

    def _connect(self):
        return sqlite3.connect(self._path, timeout=5.0)


class LocalAdapterLifecycleCoordinator:
    """Revalidates every digest; training code has no reference to this service."""

    def __init__(
        self,
        *,
        repository: LocalAdapterLifecycleRepository,
        registry: LocalAdapterRegistryPort,
        runtime: LocalAdapterRuntimeRestartPort,
        audit_sink: Callable[[str, Mapping[str, object]], None],
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._runtime = runtime
        self._audit = audit_sink
        self._promotion = LocalAdapterPromotionPolicy()
        self._rollback = LocalAdapterRollbackPolicy()

    def stage(
        self,
        bundle: LocalAdapterReleaseBundle,
        evidence: AdapterGateEvidence,
        *,
        evaluation: LocalAdapterEvaluationReport,
        shadow: ShadowEvidence,
        canary: CanaryEvidence,
        policy: LocalAdapterReleasePolicy,
    ) -> None:
        self._verify_evidence(bundle, evidence, evaluation, shadow, canary, policy)
        self._repository.stage(bundle, evidence, evaluation, shadow, canary, policy)

    @staticmethod
    def _verify_evidence(
        bundle: LocalAdapterReleaseBundle,
        evidence: AdapterGateEvidence,
        evaluation: LocalAdapterEvaluationReport,
        shadow: ShadowEvidence,
        canary: CanaryEvidence,
        policy: LocalAdapterReleasePolicy,
    ) -> None:
        if evidence.candidate_id != bundle.candidate_id or evidence.target != bundle.target:
            raise ValueError("local_adapter_stage_binding_mismatch")
        if evidence.dataset_sha256 != bundle.dataset_sha256:
            raise ValueError("local_adapter_stage_binding_mismatch")
        if policy.target != bundle.target or policy.digest != bundle.policy_sha256:
            raise ValueError("local_adapter_stage_policy_digest_mismatch")
        binding = (bundle.dataset_sha256, bundle.candidate_sha256, bundle.policy_sha256)
        if (
            evaluation.report_sha256 != bundle.evaluation_sha256
            or shadow.evidence_sha256 != bundle.shadow_sha256
            or canary.evidence_sha256 != bundle.canary_sha256
            or (
                evaluation.dataset_sha256,
                evaluation.candidate_sha256,
                evaluation.policy_sha256,
            )
            != binding
            or (shadow.dataset_sha256, shadow.candidate_sha256, shadow.policy_sha256) != binding
            or (canary.dataset_sha256, canary.candidate_sha256, canary.policy_sha256) != binding
        ):
            raise ValueError("local_adapter_stage_evidence_digest_mismatch")
        expected = {
            "golden_set_sha256": evaluation.golden_set_sha256,
            "json_validity": evaluation.json_validity,
            "known_tool_rate": evaluation.known_tool_rate,
            "required_fields_rate": evaluation.required_fields_rate,
            "argument_type_rate": evaluation.argument_type_rate,
            "known_arguments_rate": evaluation.known_arguments_rate,
            "selection_accuracy": evaluation.selection_accuracy,
            "baseline_selection_accuracy": evaluation.baseline_selection_accuracy,
            "argument_match": evaluation.argument_match,
            "baseline_argument_match": evaluation.baseline_argument_match,
            "deterministic": evaluation.deterministic,
            "safety_passed": evaluation.passed_required_slices,
            "latency_p95_ms": evaluation.latency_p95_ms,
            "memory_peak_bytes": evaluation.memory_peak_bytes,
            "slice_regressions": evaluation.slice_regressions,
            "shadow_examples": shadow.examples,
            "shadow_match_rate": shadow.matches / shadow.examples,
            "shadow_unsafe_actions": shadow.unsafe_actions,
            "canary_examples": canary.examples,
            "canary_error_rate": canary.error_rate,
            "canary_accuracy": canary.accuracy,
            "canary_escalation_rate": canary.escalation_rate,
            "canary_latency_p95_ms": canary.latency_p95_ms,
            "confidence_calibrated": evaluation.confidence_calibrated,
            "evaluation_seed": evaluation.evaluation_seed,
        }
        actual = asdict(evidence)
        if any(actual[key] != value for key, value in expected.items()):
            raise ValueError("local_adapter_stage_evidence_values_mismatch")
        policy_values = {
            "latency_limit_ms": policy.latency_limit_ms,
            "memory_limit_bytes": policy.memory_limit_bytes,
            "max_slice_regression": policy.max_slice_regression,
            "minimum_shadow_examples": policy.minimum_shadow_examples,
            "minimum_shadow_match_rate": policy.minimum_shadow_match_rate,
            "minimum_canary_examples": policy.minimum_canary_examples,
            "maximum_canary_error_rate": policy.maximum_canary_error_rate,
            "minimum_canary_accuracy": policy.minimum_canary_accuracy,
            "maximum_canary_escalation_rate": policy.maximum_canary_escalation_rate,
            "canary_latency_limit_ms": policy.canary_latency_limit_ms,
            "evaluation_seed": policy.evaluation_seed,
        }
        if any(actual[key] != value for key, value in policy_values.items()):
            raise ValueError("local_adapter_stage_policy_values_mismatch")
        if evaluation.confidence_max_brier_score != policy.maximum_confidence_brier_score:
            raise ValueError("local_adapter_stage_policy_values_mismatch")

    def promote(
        self,
        bundle: LocalAdapterReleaseBundle,
        *,
        expected_registry_revision: int,
        idempotency_key: str,
    ) -> LocalAdapterReleaseReceipt:
        with self._repository.release_transaction():
            return self._promote_locked(
                bundle,
                expected_registry_revision=expected_registry_revision,
                idempotency_key=idempotency_key,
            )

    def _promote_locked(
        self,
        bundle: LocalAdapterReleaseBundle,
        *,
        expected_registry_revision: int,
        idempotency_key: str,
    ) -> LocalAdapterReleaseReceipt:
        replay = self._repository.receipt(idempotency_key)
        if replay is not None:
            if replay.candidate_id != bundle.candidate_id or replay.bundle_sha256 != bundle.digest:
                raise ValueError("local_adapter_release_idempotency_conflict")
            if replay.status != "active":
                raise RuntimeError("local_adapter_prior_promotion_failed")
            return replay
        staged = self._repository.staged(bundle.candidate_id)
        if staged is None or staged[0] != bundle:
            raise ValueError("local_adapter_release_evidence_stale")
        self._verify_evidence(*staged)
        decision = self._promotion.evaluate(staged[1])
        if not decision.promote:
            raise ValueError("local_adapter_promotion_denied:" + ",".join(decision.reason_codes))
        promoted = self._registry.promote(
            candidate_id=bundle.candidate_id,
            expected_revision=expected_registry_revision,
            idempotency_key=idempotency_key,
            evidence_sha256=bundle.digest,
        )
        revision = _registry_revision(promoted.get("registry_revision"), fallback=0)
        try:
            restarted = self._runtime.restart(
                target=bundle.target,
                candidate_id=bundle.candidate_id,
                candidate_sha256=bundle.candidate_sha256,
            )
        except Exception:
            restarted = False
        if not restarted:
            rolled_back = self._registry.rollback(
                candidate_id=bundle.candidate_id,
                reason_code="runtime_restart_failed",
            )
            try:
                rollback_restarted = self._runtime.restart(
                    target=bundle.target,
                    candidate_id=_optional_text(rolled_back.get("rollback_target_id")),
                    candidate_sha256=_optional_digest(rolled_back.get("rollback_target_sha256")),
                )
            except Exception:
                rollback_restarted = False
            rollback_status = "rolled_back" if rollback_restarted else "rollback_restart_failed"
            self._repository.save_receipt(
                idempotency_key,
                LocalAdapterReleaseReceipt(
                    candidate_id=bundle.candidate_id,
                    target=bundle.target,
                    bundle_sha256=bundle.digest,
                    registry_revision=_registry_revision(
                        rolled_back.get("registry_revision"),
                        fallback=revision,
                    ),
                    status=rollback_status,
                    completed_at=_now(),
                ),
            )
            self._audit(
                "local_adapter_promotion_compensated",
                {
                    "candidate_id": bundle.candidate_id,
                    "bundle_sha256": bundle.digest,
                    "reason_code": (
                        "runtime_restart_failed" if rollback_restarted else "rollback_runtime_restart_failed"
                    ),
                },
            )
            raise RuntimeError(
                "local_adapter_runtime_restart_failed"
                if rollback_restarted
                else "local_adapter_rollback_runtime_restart_failed"
            )
        receipt = LocalAdapterReleaseReceipt(
            candidate_id=bundle.candidate_id,
            target=bundle.target,
            bundle_sha256=bundle.digest,
            registry_revision=revision,
            status="active",
            completed_at=_now(),
        )
        self._repository.save_receipt(idempotency_key, receipt)
        self._audit(
            "local_adapter_promoted",
            {
                "candidate_id": bundle.candidate_id,
                "bundle_sha256": bundle.digest,
                "registry_revision": revision,
            },
        )
        return receipt

    def reconcile_live(
        self,
        bundle: LocalAdapterReleaseBundle,
        signals: LiveAdapterSignals,
    ) -> bool:
        with self._repository.release_transaction():
            decision = self._rollback.evaluate(signals)
            if decision.promote:
                return False
            reason = decision.reason_codes[0]
            rolled_back = self._registry.rollback(candidate_id=bundle.candidate_id, reason_code=reason)
            if not self._runtime.restart(
                target=bundle.target,
                candidate_id=_optional_text(rolled_back.get("rollback_target_id")),
                candidate_sha256=_optional_digest(rolled_back.get("rollback_target_sha256")),
            ):
                self._audit(
                    "local_adapter_rollback_restart_failed",
                    {
                        "candidate_id": bundle.candidate_id,
                        "bundle_sha256": bundle.digest,
                        "reason_code": reason,
                    },
                )
                raise RuntimeError("local_adapter_rollback_restart_failed")
            self._audit(
                "local_adapter_rolled_back",
                {
                    "candidate_id": bundle.candidate_id,
                    "bundle_sha256": bundle.digest,
                    "reason_code": reason,
                },
            )
            return True


def _digest(value: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("local_adapter_release_digest_invalid")
    return normalized


def _registry_revision(value: object, *, fallback: int) -> int:
    if value is None:
        return fallback
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise RuntimeError("local_adapter_registry_revision_invalid")
    try:
        revision = int(value)
    except ValueError as exc:
        raise RuntimeError("local_adapter_registry_revision_invalid") from exc
    if revision < 0:
        raise RuntimeError("local_adapter_registry_revision_invalid")
    return revision


def _optional_text(value: object) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _optional_digest(value: object) -> str | None:
    normalized = str(value or "").strip()
    return _digest(normalized) if normalized else None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "LocalAdapterLifecycleCoordinator",
    "LocalAdapterLifecycleRepository",
    "LocalAdapterRegistryPort",
    "LocalAdapterReleaseBundle",
    "LocalAdapterReleaseReceipt",
    "LocalAdapterRuntimeRestartPort",
]
