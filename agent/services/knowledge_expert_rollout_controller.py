"""Persistent Hub-owned automatic rollout and rollback for knowledge experts."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from agent.services.interprocess_file_transaction import InterProcessFileTransaction


class KnowledgeExpertGenerationSwitchPort(Protocol):
    def switch(
        self,
        *,
        bank_id: str,
        expected_generation_id: str,
        target_generation_id: str,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class KnowledgeExpertRolloutPolicy:
    minimum_shadow_observations: int = 100
    minimum_canary_observations: int = 100
    canary_basis_points: int = 500
    maximum_error_rate: float = 0.02
    minimum_quality_delta: float = 0.0
    maximum_p95_latency_ms: float = 2500.0

    def __post_init__(self) -> None:
        if self.minimum_shadow_observations < 1 or self.minimum_canary_observations < 1:
            raise ValueError("knowledge_expert_rollout_observation_count_invalid")
        if not 1 <= self.canary_basis_points <= 10_000:
            raise ValueError("knowledge_expert_rollout_canary_percentage_invalid")
        if not _bounded(self.maximum_error_rate, 0.0, 1.0):
            raise ValueError("knowledge_expert_rollout_error_rate_invalid")
        if not _bounded(self.minimum_quality_delta, -1.0, 1.0):
            raise ValueError("knowledge_expert_rollout_quality_delta_invalid")
        if not _bounded(self.maximum_p95_latency_ms, 0.0, math.inf):
            raise ValueError("knowledge_expert_rollout_latency_invalid")

    @property
    def digest(self) -> str:
        return _sha256(asdict(self))


@dataclass(frozen=True, slots=True)
class KnowledgeExpertRolloutAdmission:
    bank_id: str
    candidate_generation_id: str
    last_good_generation_id: str
    research_reproduction_passed: bool
    runtime_capability_passed: bool
    security_passed: bool
    benchmark_passed: bool
    operations_passed: bool

    def validate(self) -> None:
        for value in (self.bank_id, self.candidate_generation_id, self.last_good_generation_id):
            if not str(value).strip() or len(str(value)) > 192:
                raise ValueError("knowledge_expert_rollout_admission_binding_invalid")
        if self.candidate_generation_id == self.last_good_generation_id:
            raise ValueError("knowledge_expert_rollout_admission_generation_invalid")

    @property
    def passed(self) -> bool:
        return all(
            (
                self.research_reproduction_passed,
                self.runtime_capability_passed,
                self.security_passed,
                self.benchmark_passed,
                self.operations_passed,
            )
        )

    @property
    def digest(self) -> str:
        return _sha256(asdict(self))


@dataclass(frozen=True, slots=True)
class KnowledgeExpertRolloutObservation:
    observation_id: str
    stage: str
    success: bool
    quality_delta: float
    latency_ms: float
    conflict_detected: bool = False
    hallucination_detected: bool = False
    oom_detected: bool = False
    cache_error: bool = False
    security_event: bool = False
    scope_violation: bool = False

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or len(self.observation_id) > 192:
            raise ValueError("knowledge_expert_rollout_observation_id_invalid")
        if self.stage not in {"shadow", "canary", "ga"}:
            raise ValueError("knowledge_expert_rollout_observation_stage_invalid")
        if not _bounded(self.quality_delta, -1.0, 1.0):
            raise ValueError("knowledge_expert_rollout_quality_delta_invalid")
        if not _bounded(self.latency_ms, 0.0, math.inf):
            raise ValueError("knowledge_expert_rollout_latency_invalid")


class KnowledgeExpertRolloutController:
    """Advance one scoped release and roll it back without interactive waits."""

    def __init__(
        self,
        path: str | Path,
        *,
        generation_switch: KnowledgeExpertGenerationSwitchPort,
        policy: KnowledgeExpertRolloutPolicy | None = None,
    ) -> None:
        self._path = Path(path)
        self._generation_switch = generation_switch
        self._policy = policy or KnowledgeExpertRolloutPolicy()
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def admit(self, *, scope_id: str, admission: KnowledgeExpertRolloutAdmission) -> dict[str, Any]:
        scope = _scope(scope_id)
        admission.validate()
        with self._transaction, self._connect() as connection:
            current = self._state(connection, scope)
            if not admission.passed:
                return self._save(
                    connection,
                    scope,
                    {
                        **_fresh_state(scope, self._policy),
                        "revision": current["revision"] + 1,
                        "reason_code": "knowledge_expert_rollout_admission_failed",
                    },
                )
            if current["stage"] != "off":
                if current["admission_digest"] != admission.digest:
                    raise ValueError("knowledge_expert_rollout_admission_conflict")
                return current
            state = {
                **_fresh_state(scope, self._policy),
                "stage": "shadow",
                "reason_code": "knowledge_expert_shadow_started",
                "revision": current["revision"] + 1,
                "bank_id": admission.bank_id,
                "candidate_generation_id": admission.candidate_generation_id,
                "last_good_generation_id": admission.last_good_generation_id,
                "admission_digest": admission.digest,
            }
            return self._save(connection, scope, state)

    def observe(self, *, scope_id: str, observation: KnowledgeExpertRolloutObservation) -> dict[str, Any]:
        scope = _scope(scope_id)
        with self._transaction, self._connect() as connection:
            current = self._state(connection, scope)
            if connection.execute(
                "SELECT 1 FROM knowledge_expert_rollout_observations WHERE scope_id=? AND observation_id=?",
                (scope, observation.observation_id),
            ).fetchone():
                return current
            if current["stage"] != observation.stage:
                raise ValueError("knowledge_expert_rollout_observation_stage_stale")
            connection.execute(
                "INSERT INTO knowledge_expert_rollout_observations(scope_id, observation_id) VALUES (?, ?)",
                (scope, observation.observation_id),
            )
            reason = self._immediate_stop_reason(observation)
            if reason:
                return self._stop(connection, scope, current, reason)
            updated = {
                **current,
                "observation_count": int(current["observation_count"]) + 1,
                "error_count": int(current["error_count"]) + (0 if observation.success else 1),
                "quality_delta_sum": float(current["quality_delta_sum"]) + observation.quality_delta,
                "latency_samples": [*list(current["latency_samples"]), observation.latency_ms][-10_000:],
                "revision": int(current["revision"]) + 1,
                "reason_code": "knowledge_expert_rollout_observation_recorded",
            }
            reason = self._aggregate_stop_reason(updated)
            if reason:
                return self._stop(connection, scope, updated, reason)
            minimum = (
                self._policy.minimum_shadow_observations
                if observation.stage == "shadow"
                else self._policy.minimum_canary_observations
            )
            if observation.stage in {"shadow", "canary"} and updated["observation_count"] >= minimum:
                if observation.stage == "shadow" and not self._generation_switch.switch(
                    bank_id=str(updated["bank_id"]),
                    expected_generation_id=str(updated["last_good_generation_id"]),
                    target_generation_id=str(updated["candidate_generation_id"]),
                ):
                    return self._stop(
                        connection,
                        scope,
                        updated,
                        "knowledge_expert_candidate_activation_failed",
                    )
                next_stage = "canary" if observation.stage == "shadow" else "ga"
                updated = {
                    **updated,
                    "stage": next_stage,
                    "reason_code": f"knowledge_expert_{next_stage}_automatic",
                    "observation_count": 0,
                    "error_count": 0,
                    "quality_delta_sum": 0.0,
                    "latency_samples": [],
                }
            return self._save(connection, scope, updated)

    def assignment(self, *, scope_id: str, request_id: str) -> dict[str, Any]:
        scope = _scope(scope_id)
        request = str(request_id or "").strip()
        if not request or len(request) > 256:
            raise ValueError("knowledge_expert_rollout_request_id_invalid")
        with self._connect() as connection:
            state = self._state(connection, scope)
        stage = str(state["stage"])
        if stage == "ga":
            affecting = True
        elif stage == "canary":
            bucket = int.from_bytes(hashlib.sha256(f"{scope}\0{request}".encode()).digest()[:8], "big") % 10_000
            affecting = bucket < self._policy.canary_basis_points
        else:
            affecting = False
        return {
            "schema": "ananta.knowledge-expert-rollout-decision.v1",
            "stage": stage,
            "result_affecting": affecting,
            "generation_id": state["candidate_generation_id"] if affecting else state["last_good_generation_id"],
            "revision": state["revision"],
            "reason_code": state["reason_code"],
        }

    def snapshot(self, *, scope_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            return self._state(connection, _scope(scope_id))

    def _stop(
        self,
        connection: sqlite3.Connection,
        scope: str,
        current: Mapping[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        rollback_required = current.get("stage") in {"canary", "ga"}
        rollback_ok = True
        if rollback_required:
            rollback_ok = self._generation_switch.switch(
                bank_id=str(current["bank_id"]),
                expected_generation_id=str(current["candidate_generation_id"]),
                target_generation_id=str(current["last_good_generation_id"]),
            )
        stopped = {
            **_fresh_state(scope, self._policy),
            "revision": int(current["revision"]) + 1,
            "reason_code": reason if rollback_ok else "knowledge_expert_automatic_rollback_failed",
            "last_good_generation_id": str(current.get("last_good_generation_id") or ""),
        }
        return self._save(connection, scope, stopped)

    @staticmethod
    def _immediate_stop_reason(observation: KnowledgeExpertRolloutObservation) -> str | None:
        for detected, reason in (
            (observation.security_event, "knowledge_expert_security_event"),
            (observation.scope_violation, "knowledge_expert_scope_violation"),
            (observation.conflict_detected, "knowledge_expert_conflict_detected"),
            (observation.hallucination_detected, "knowledge_expert_hallucination_detected"),
            (observation.oom_detected, "knowledge_expert_oom_detected"),
            (observation.cache_error, "knowledge_expert_cache_error"),
        ):
            if detected:
                return reason
        return None

    def _aggregate_stop_reason(self, state: Mapping[str, Any]) -> str | None:
        count = int(state["observation_count"])
        if int(state["error_count"]) / count > self._policy.maximum_error_rate:
            return "knowledge_expert_error_rate_exceeded"
        if float(state["quality_delta_sum"]) / count < self._policy.minimum_quality_delta:
            return "knowledge_expert_quality_regression"
        if _p95(list(state["latency_samples"])) > self._policy.maximum_p95_latency_ms:
            return "knowledge_expert_latency_exceeded"
        return None

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_expert_rollout_state("
                "scope_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_expert_rollout_observations("
                "scope_id TEXT NOT NULL, observation_id TEXT NOT NULL, PRIMARY KEY(scope_id, observation_id))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)

    def _state(self, connection: sqlite3.Connection, scope: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT payload_json FROM knowledge_expert_rollout_state WHERE scope_id=?",
            (scope,),
        ).fetchone()
        state = json.loads(row[0]) if row else _fresh_state(scope, self._policy)
        if state.get("policy_sha256") != self._policy.digest:
            return _fresh_state(scope, self._policy)
        return state

    @staticmethod
    def _save(connection: sqlite3.Connection, scope: str, state: Mapping[str, Any]) -> dict[str, Any]:
        connection.execute(
            "INSERT INTO knowledge_expert_rollout_state(scope_id,payload_json) VALUES (?,?) "
            "ON CONFLICT(scope_id) DO UPDATE SET payload_json=excluded.payload_json",
            (scope, json.dumps(dict(state), sort_keys=True, separators=(",", ":"), allow_nan=False)),
        )
        return dict(state)


def _fresh_state(scope: str, policy: KnowledgeExpertRolloutPolicy) -> dict[str, Any]:
    return {
        "schema": "ananta.knowledge-expert-rollout-state.v1",
        "scope_id": scope,
        "stage": "off",
        "reason_code": "knowledge_expert_rollout_not_admitted",
        "revision": 0,
        "bank_id": "",
        "candidate_generation_id": "",
        "last_good_generation_id": "",
        "admission_digest": "",
        "policy_sha256": policy.digest,
        "observation_count": 0,
        "error_count": 0,
        "quality_delta_sum": 0.0,
        "latency_samples": [],
    }


def _scope(value: str) -> str:
    scope = str(value or "").strip()
    if not scope or len(scope) > 256:
        raise ValueError("knowledge_expert_rollout_scope_invalid")
    return scope


def _bounded(value: object, minimum: float, maximum: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and minimum <= float(value) <= maximum
    )


def _p95(values: list[float]) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = [
    "KnowledgeExpertGenerationSwitchPort",
    "KnowledgeExpertRolloutAdmission",
    "KnowledgeExpertRolloutController",
    "KnowledgeExpertRolloutObservation",
    "KnowledgeExpertRolloutPolicy",
]
