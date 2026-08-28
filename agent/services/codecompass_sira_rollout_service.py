"""Persistent Hub-owned automatic rollout control for CodeCompass SIRA."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from agent.services.codecompass_sira_evaluation_gate import CodeCompassSiraEvaluationGate
from agent.services.interprocess_file_transaction import InterProcessFileTransaction

_STAGES = frozenset({"off", "shadow", "canary", "preferred"})


@dataclass(frozen=True, slots=True)
class SiraRolloutPolicy:
    minimum_shadow_observations: int = 100
    minimum_canary_observations: int = 100
    canary_basis_points: int = 500
    maximum_error_rate: float = 0.02
    minimum_quality_delta: float = 0.0
    maximum_p95_latency_ms: float = 2500.0
    maximum_tokens_per_query: float = 2048.0
    maximum_cost_per_query: float = 0.01

    def __post_init__(self) -> None:
        if self.minimum_shadow_observations < 1 or self.minimum_canary_observations < 1:
            raise ValueError("sira_rollout_observation_count_invalid")
        if not 1 <= self.canary_basis_points <= 10_000:
            raise ValueError("sira_rollout_canary_percentage_invalid")
        for value, reason in (
            (self.maximum_error_rate, "sira_rollout_error_rate_invalid"),
            (self.minimum_quality_delta, "sira_rollout_quality_delta_invalid"),
        ):
            if not math.isfinite(float(value)) or not -1.0 <= float(value) <= 1.0:
                raise ValueError(reason)
        for value in (
            self.maximum_p95_latency_ms,
            self.maximum_tokens_per_query,
            self.maximum_cost_per_query,
        ):
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError("sira_rollout_budget_invalid")

    @property
    def digest(self) -> str:
        return _sha256(asdict(self))


@dataclass(frozen=True, slots=True)
class SiraRolloutObservation:
    observation_id: str
    stage: str
    success: bool
    quality_delta: float
    latency_ms: float
    tokens: int
    cost: float
    exact_regression: bool = False
    security_regression: bool = False
    scope_regression: bool = False
    index_compatible: bool = True
    model_available: bool = True
    delta_complete: bool = True

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or len(self.observation_id) > 192:
            raise ValueError("sira_rollout_observation_id_invalid")
        if self.stage not in {"shadow", "canary"}:
            raise ValueError("sira_rollout_observation_stage_invalid")
        if not math.isfinite(float(self.quality_delta)) or not -1.0 <= float(self.quality_delta) <= 1.0:
            raise ValueError("sira_rollout_quality_delta_invalid")
        if not math.isfinite(float(self.latency_ms)) or float(self.latency_ms) < 0.0:
            raise ValueError("sira_rollout_latency_invalid")
        if isinstance(self.tokens, bool) or not isinstance(self.tokens, int) or self.tokens < 0:
            raise ValueError("sira_rollout_tokens_invalid")
        if not math.isfinite(float(self.cost)) or float(self.cost) < 0.0:
            raise ValueError("sira_rollout_cost_invalid")


@dataclass(frozen=True, slots=True)
class SiraRolloutDecision:
    stage: str
    result_affecting: bool
    reason_code: str
    revision: int

    def to_dict(self) -> dict[str, Any]:
        return {"schema": "codecompass.sira-rollout-decision.v1", **asdict(self)}


class CodeCompassSiraRolloutService:
    """Advances and stops SIRA from redacted observations without people."""

    def __init__(
        self,
        path: str | Path,
        *,
        policy: SiraRolloutPolicy | None = None,
        evaluation_gate: CodeCompassSiraEvaluationGate | None = None,
    ) -> None:
        self._path = Path(path)
        self._policy = policy or SiraRolloutPolicy()
        self._evaluation_gate = evaluation_gate or CodeCompassSiraEvaluationGate()
        self._transaction = InterProcessFileTransaction(self._path.with_suffix(".lock"))
        self._initialize()

    def admit_benchmark(
        self,
        *,
        scope_id: str,
        report: Mapping[str, Any],
        evaluation_policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        scope = _scope(scope_id)
        gate = self._evaluation_gate.assess(report, evaluation_policy)
        with self._transaction, self._connect() as connection:
            current = self._state(connection, scope)
            if not gate.passed:
                return self._stop(
                    connection,
                    scope,
                    current,
                    reason_code="sira_benchmark_gate_failed",
                )
            if current["stage"] in {"shadow", "canary", "preferred"}:
                if current["benchmark_policy_sha256"] != gate.policy_sha256:
                    raise ValueError("sira_rollout_benchmark_binding_conflict")
                return current
            return self._save(
                connection,
                scope,
                {
                    **_fresh_state(scope, self._policy),
                    "stage": "shadow",
                    "reason_code": "sira_shadow_started",
                    "benchmark_policy_sha256": gate.policy_sha256,
                    "revision": current["revision"] + 1,
                },
            )

    def observe(self, *, scope_id: str, observation: SiraRolloutObservation) -> dict[str, Any]:
        scope = _scope(scope_id)
        with self._transaction, self._connect() as connection:
            current = self._state(connection, scope)
            replay = connection.execute(
                "SELECT 1 FROM sira_rollout_observations WHERE scope_id = ? AND observation_id = ?",
                (scope, observation.observation_id),
            ).fetchone()
            if replay is not None:
                return current
            if current["stage"] != observation.stage:
                raise ValueError("sira_rollout_observation_stage_stale")
            connection.execute(
                "INSERT INTO sira_rollout_observations(scope_id, observation_id) VALUES (?, ?)",
                (scope, observation.observation_id),
            )
            stop_reason = self._stop_reason(observation)
            if stop_reason:
                return self._stop(connection, scope, current, reason_code=stop_reason)
            count = int(current["observation_count"]) + 1
            errors = int(current["error_count"]) + (0 if observation.success else 1)
            updated = {
                **current,
                "observation_count": count,
                "error_count": errors,
                "quality_delta_sum": float(current["quality_delta_sum"]) + observation.quality_delta,
                "latency_samples": [*list(current["latency_samples"]), observation.latency_ms][-10_000:],
                "tokens_sum": int(current["tokens_sum"]) + observation.tokens,
                "cost_sum": float(current["cost_sum"]) + observation.cost,
                "revision": int(current["revision"]) + 1,
                "reason_code": "sira_rollout_observation_recorded",
            }
            aggregate_reason = self._aggregate_stop_reason(updated)
            if aggregate_reason:
                return self._stop(connection, scope, updated, reason_code=aggregate_reason)
            minimum = (
                self._policy.minimum_shadow_observations
                if observation.stage == "shadow"
                else self._policy.minimum_canary_observations
            )
            if count >= minimum:
                next_stage = "canary" if observation.stage == "shadow" else "preferred"
                updated = {
                    **updated,
                    "stage": next_stage,
                    "reason_code": f"sira_{next_stage}_automatic",
                    "observation_count": 0,
                    "error_count": 0,
                    "quality_delta_sum": 0.0,
                    "latency_samples": [],
                    "tokens_sum": 0,
                    "cost_sum": 0.0,
                }
            return self._save(connection, scope, updated)

    def assignment(self, *, scope_id: str, request_id: str) -> SiraRolloutDecision:
        scope = _scope(scope_id)
        request = str(request_id or "").strip()
        if not request or len(request) > 256:
            raise ValueError("sira_rollout_request_id_invalid")
        with self._connect() as connection:
            state = self._state(connection, scope)
        stage = state["stage"]
        if stage == "preferred":
            affecting = True
            reason = "sira_preferred_selected"
        elif stage == "canary":
            bucket = int.from_bytes(hashlib.sha256(f"{scope}\0{request}".encode()).digest()[:8], "big") % 10_000
            affecting = bucket < self._policy.canary_basis_points
            reason = "sira_canary_selected" if affecting else "sira_canary_baseline_selected"
        elif stage == "shadow":
            affecting = False
            reason = "sira_shadow_non_effecting"
        else:
            affecting = False
            reason = str(state["reason_code"] or "sira_rollout_off")
        return SiraRolloutDecision(stage, affecting, reason, int(state["revision"]))

    def snapshot(self, *, scope_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            state = self._state(connection, _scope(scope_id))
        return {**state, "policy_sha256": self._policy.digest}

    def retrieval_profile(
        self,
        *,
        scope_id: str,
        request_id: str,
        corpus_ready: bool,
    ) -> dict[str, Any]:
        """Build the only Hub-owned profile that may select Worker SIRA."""

        decision = self.assignment(scope_id=scope_id, request_id=request_id)
        if decision.stage == "off" or (decision.stage == "canary" and not decision.result_affecting):
            return {}
        return {
            "name": "corpus_discriminative_lexical",
            "corpus_ready": bool(corpus_ready),
            "rollout": decision.to_dict(),
        }

    def _stop_reason(self, observation: SiraRolloutObservation) -> str | None:
        if observation.security_regression:
            return "sira_security_regression"
        if observation.scope_regression:
            return "sira_scope_regression"
        if observation.exact_regression:
            return "sira_exact_query_regression"
        if not observation.index_compatible:
            return "sira_index_incompatible"
        if not observation.delta_complete:
            return "sira_partial_delta"
        if not observation.model_available:
            return "sira_model_unavailable"
        if observation.latency_ms > self._policy.maximum_p95_latency_ms:
            return "sira_latency_budget_exceeded"
        if observation.tokens > self._policy.maximum_tokens_per_query:
            return "sira_token_budget_exceeded"
        if observation.cost > self._policy.maximum_cost_per_query:
            return "sira_cost_budget_exceeded"
        return None

    def _aggregate_stop_reason(self, state: Mapping[str, Any]) -> str | None:
        count = int(state["observation_count"])
        if int(state["error_count"]) / count > self._policy.maximum_error_rate:
            return "sira_error_rate_exceeded"
        if float(state["quality_delta_sum"]) / count < self._policy.minimum_quality_delta:
            return "sira_quality_regression"
        if _p95(list(state["latency_samples"])) > self._policy.maximum_p95_latency_ms:
            return "sira_latency_budget_exceeded"
        return None

    def _stop(
        self,
        connection: sqlite3.Connection,
        scope: str,
        current: Mapping[str, Any],
        *,
        reason_code: str,
    ) -> dict[str, Any]:
        return self._save(
            connection,
            scope,
            {
                **_fresh_state(scope, self._policy),
                "stage": "off",
                "reason_code": reason_code,
                "revision": int(current["revision"]) + 1,
            },
        )

    def _save(self, connection: sqlite3.Connection, scope: str, state: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.dumps(dict(state), sort_keys=True, separators=(",", ":"), allow_nan=False)
        connection.execute(
            "INSERT INTO sira_rollout_state(scope_id, payload_json) VALUES (?, ?) "
            "ON CONFLICT(scope_id) DO UPDATE SET payload_json=excluded.payload_json",
            (scope, payload),
        )
        return dict(state)

    def _state(self, connection: sqlite3.Connection, scope: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT payload_json FROM sira_rollout_state WHERE scope_id = ?",
            (scope,),
        ).fetchone()
        state = json.loads(row[0]) if row else _fresh_state(scope, self._policy)
        if state.get("stage") not in _STAGES or state.get("policy_sha256") != self._policy.digest:
            return _fresh_state(scope, self._policy)
        return state

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sira_rollout_state(scope_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sira_rollout_observations("
                "scope_id TEXT NOT NULL, observation_id TEXT NOT NULL, PRIMARY KEY(scope_id, observation_id))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path, timeout=5.0)


def _fresh_state(scope: str, policy: SiraRolloutPolicy) -> dict[str, Any]:
    return {
        "schema": "codecompass.sira-rollout-state.v1",
        "scope_id": scope,
        "stage": "off",
        "reason_code": "sira_rollout_not_admitted",
        "revision": 0,
        "benchmark_policy_sha256": "",
        "policy_sha256": policy.digest,
        "observation_count": 0,
        "error_count": 0,
        "quality_delta_sum": 0.0,
        "latency_samples": [],
        "tokens_sum": 0,
        "cost_sum": 0.0,
    }


def _scope(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256:
        raise ValueError("sira_rollout_scope_invalid")
    return normalized


def _sha256(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


__all__ = [
    "CodeCompassSiraRolloutService",
    "SiraRolloutDecision",
    "SiraRolloutObservation",
    "SiraRolloutPolicy",
]
