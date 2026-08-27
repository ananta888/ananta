"""Persistent side-effect-free shadow and fenced low-risk canary controls."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from agent.services.local_adapter_lifecycle import LocalAdapterReleasePolicy
from agent.services.local_tool_training_redaction import LocalToolTrainingRedactionPolicy

_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")


class CanaryLeasePort(Protocol):
    def valid(self, *, lease_id: str, fencing_token: int, policy_sha256: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class ShadowEvidence:
    dataset_sha256: str
    candidate_sha256: str
    policy_sha256: str
    examples: int
    matches: int
    unsafe_actions: int
    evidence_sha256: str

    def __post_init__(self) -> None:
        _validate_evidence_binding(self.dataset_sha256, self.candidate_sha256, self.policy_sha256, self.evidence_sha256)
        for name in ("examples", "matches", "unsafe_actions"):
            _nonnegative_int(getattr(self, name), name)
        if self.matches > self.examples or self.unsafe_actions > self.examples:
            raise ValueError("local_adapter_shadow_counts_invalid")


@dataclass(frozen=True, slots=True)
class CanaryAuthorization:
    admitted: bool
    reason_code: str
    interaction_id: str
    tool_name: str
    dry_run: bool
    fencing_token: int


@dataclass(frozen=True, slots=True)
class CanaryEvidence:
    dataset_sha256: str
    candidate_sha256: str
    policy_sha256: str
    examples: int
    error_rate: float
    accuracy: float
    escalation_rate: float
    latency_p95_ms: int
    slice_metrics: Mapping[str, Mapping[str, float | int]]
    evidence_sha256: str

    def __post_init__(self) -> None:
        _validate_evidence_binding(self.dataset_sha256, self.candidate_sha256, self.policy_sha256, self.evidence_sha256)
        _nonnegative_int(self.examples, "examples")
        _nonnegative_int(self.latency_p95_ms, "latency_p95_ms")
        for name in ("error_rate", "accuracy", "escalation_rate"):
            _rate(getattr(self, name), name)
        for slice_id, metrics in self.slice_metrics.items():
            if not str(slice_id).strip() or not isinstance(metrics, Mapping):
                raise ValueError("local_adapter_canary_slice_metrics_invalid")
            for key, value in metrics.items():
                if key in {"examples", "latency_max_ms"}:
                    _nonnegative_int(value, "slice_examples")
                else:
                    _rate(value, f"slice_{key}")


class LocalAdapterRolloutRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._initialize()

    def append(self, table: str, values: Mapping[str, object]) -> None:
        if table not in {"shadow_observations", "canary_observations", "canary_outcomes"}:
            raise ValueError("local_adapter_rollout_table_invalid")
        with self._lock, self._connect() as connection:
            connection.execute(
                f"INSERT INTO {table}(record_json) VALUES (?)",  # noqa: S608 - table is exact allowlist
                (json.dumps(dict(values), sort_keys=True, separators=(",", ":"), allow_nan=False),),
            )

    def append_interaction_once(self, table: str, values: Mapping[str, object]) -> None:
        """Persist one immutable observation per interaction and binding."""

        if table not in {"shadow_observations", "canary_observations", "canary_outcomes"}:
            raise ValueError("local_adapter_rollout_table_invalid")
        payload = dict(values)
        identity = (
            str(payload.get("interaction_id") or ""),
            str(payload.get("dataset_sha256") or ""),
            str(payload.get("candidate_sha256") or ""),
            str(payload.get("policy_sha256") or ""),
        )
        if not all(identity):
            raise ValueError("local_adapter_rollout_identity_invalid")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                f"SELECT record_json FROM {table}",  # noqa: S608 - table is exact allowlist
            ).fetchall()
            for row in rows:
                existing = json.loads(row[0])
                existing_identity = (
                    str(existing.get("interaction_id") or ""),
                    str(existing.get("dataset_sha256") or ""),
                    str(existing.get("candidate_sha256") or ""),
                    str(existing.get("policy_sha256") or ""),
                )
                if existing_identity != identity:
                    continue
                if row[0] == canonical:
                    return
                raise ValueError("local_adapter_rollout_interaction_conflict")
            connection.execute(
                f"INSERT INTO {table}(record_json) VALUES (?)",  # noqa: S608 - table is exact allowlist
                (canonical,),
            )

    def read(self, table: str) -> tuple[dict[str, Any], ...]:
        if table not in {"shadow_observations", "canary_observations", "canary_outcomes"}:
            raise ValueError("local_adapter_rollout_table_invalid")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"SELECT record_json FROM {table} ORDER BY rowid",  # noqa: S608 - table is exact allowlist
            ).fetchall()
        return tuple(json.loads(row[0]) for row in rows)

    def _initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS shadow_observations(record_json TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS canary_observations(record_json TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS canary_outcomes(record_json TEXT NOT NULL)")

    def _connect(self):
        return sqlite3.connect(self._path, timeout=5.0)


class LocalAdapterShadowController:
    """Compares decisions and deliberately exposes no execution dependency."""

    def __init__(
        self,
        repository: LocalAdapterRolloutRepository,
        *,
        redaction: LocalToolTrainingRedactionPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._redaction = redaction or LocalToolTrainingRedactionPolicy()

    def observe(
        self,
        *,
        interaction_id: str,
        dataset_sha256: str,
        candidate_sha256: str,
        policy_sha256: str,
        production: Mapping[str, Any],
        candidate: Mapping[str, Any],
        production_latency_ms: int,
        candidate_latency_ms: int,
    ) -> None:
        raw_production_arguments = production.get("arguments")
        raw_candidate_arguments = candidate.get("arguments")
        production_arguments = self._redaction.sanitize_arguments(
            raw_production_arguments if isinstance(raw_production_arguments, Mapping) else {}
        )
        candidate_arguments = self._redaction.sanitize_arguments(
            raw_candidate_arguments if isinstance(raw_candidate_arguments, Mapping) else {}
        )
        production_tool = str(production.get("tool") or "").strip().lower() or None
        candidate_tool = str(candidate.get("tool") or "").strip().lower() or None
        self._repository.append_interaction_once(
            "shadow_observations",
            {
                "interaction_id": interaction_id,
                "dataset_sha256": _digest(dataset_sha256),
                "candidate_sha256": _digest(candidate_sha256),
                "policy_sha256": _digest(policy_sha256),
                "production_tool": production_tool,
                "candidate_tool": candidate_tool,
                "production_arguments": production_arguments,
                "candidate_arguments": candidate_arguments,
                "production_latency_ms": max(0, int(production_latency_ms)),
                "candidate_latency_ms": max(0, int(candidate_latency_ms)),
                "matches": production_tool == candidate_tool and production_arguments == candidate_arguments,
                "unsafe_action": candidate_tool is not None
                and candidate.get("risk_class") not in {"read", "idempotent", "simulated"},
                "observed_at": _now(),
            },
        )

    def evidence(
        self,
        *,
        dataset_sha256: str,
        candidate_sha256: str,
        release_policy: LocalAdapterReleasePolicy,
    ) -> ShadowEvidence:
        if not isinstance(release_policy, LocalAdapterReleasePolicy):
            raise ValueError("shadow_release_policy_invalid")
        policy_sha256 = release_policy.digest
        binding = (_digest(dataset_sha256), _digest(candidate_sha256), _digest(policy_sha256))
        rows = tuple(
            row
            for row in self._repository.read("shadow_observations")
            if (row["dataset_sha256"], row["candidate_sha256"], row["policy_sha256"]) == binding
        )
        if len(rows) < release_policy.minimum_shadow_examples:
            raise ValueError("shadow_minimum_examples_not_met")
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return ShadowEvidence(
            dataset_sha256=binding[0],
            candidate_sha256=binding[1],
            policy_sha256=binding[2],
            examples=len(rows),
            matches=sum(row["matches"] is True for row in rows),
            unsafe_actions=sum(row["unsafe_action"] is True for row in rows),
            evidence_sha256=hashlib.sha256(canonical).hexdigest(),
        )


class LocalAdapterCanaryController:
    """Hub admission only; the existing tool gateway remains execution authority."""

    def __init__(
        self,
        repository: LocalAdapterRolloutRepository,
        *,
        leases: CanaryLeasePort,
        release_policy: LocalAdapterReleasePolicy,
        dataset_sha256: str,
        candidate_sha256: str,
        expires_at: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(release_policy, LocalAdapterReleasePolicy):
            raise ValueError("canary_release_policy_invalid")
        self._repository = repository
        self._leases = leases
        self._traffic = release_policy.canary_traffic_basis_points
        self._tools = frozenset(release_policy.canary_allowed_tools)
        self._minimum_examples = release_policy.minimum_canary_examples
        self._dataset = _digest(dataset_sha256)
        self._candidate = _digest(candidate_sha256)
        self._policy = release_policy.digest
        self._clock = clock or (lambda: datetime.now(UTC))
        self._expires_at = _time(expires_at)
        now = self._clock().astimezone(UTC)
        duration = (self._expires_at - now).total_seconds()
        if not 0 < duration <= release_policy.canary_maximum_duration_seconds:
            raise ValueError("canary_duration_invalid")

    def authorize(
        self,
        *,
        interaction_id: str,
        tool_name: str,
        risk_class: str,
        dry_run: bool,
        lease_id: str,
        fencing_token: int,
    ) -> CanaryAuthorization:
        normalized_interaction = str(interaction_id or "").strip().lower()
        normalized_tool = str(tool_name or "").strip().lower()
        normalized_lease = str(lease_id or "").strip().lower()
        if (
            _IDENTIFIER.fullmatch(normalized_interaction) is None
            or _IDENTIFIER.fullmatch(normalized_tool) is None
            or _IDENTIFIER.fullmatch(normalized_lease) is None
        ):
            raise ValueError("canary_authorization_identity_invalid")
        if not isinstance(dry_run, bool):
            raise ValueError("canary_dry_run_invalid")
        if isinstance(fencing_token, bool) or not isinstance(fencing_token, int) or fencing_token < 1:
            raise ValueError("canary_fencing_token_invalid")
        interaction_id = normalized_interaction
        tool_name = normalized_tool
        lease_id = normalized_lease
        reason = "canary_admitted"
        admitted = True
        if self._clock().astimezone(UTC) >= self._expires_at:
            admitted, reason = False, "canary_expired"
        elif not self._leases.valid(lease_id=lease_id, fencing_token=fencing_token, policy_sha256=self._policy):
            admitted, reason = False, "canary_lease_invalid"
        elif tool_name not in self._tools:
            admitted, reason = False, "canary_tool_not_allowed"
        elif risk_class not in {"read", "idempotent", "simulated"} and not (risk_class == "write" and dry_run):
            admitted, reason = False, "canary_side_effect_blocked"
        elif int(hashlib.sha256(interaction_id.encode()).hexdigest()[:8], 16) % 10_000 >= self._traffic:
            admitted, reason = False, "canary_not_sampled"
        authorization = CanaryAuthorization(
            admitted=admitted,
            reason_code=reason,
            interaction_id=interaction_id,
            tool_name=tool_name,
            dry_run=dry_run,
            fencing_token=fencing_token,
        )
        self._repository.append_interaction_once(
            "canary_observations",
            {
                **asdict(authorization),
                "dataset_sha256": self._dataset,
                "candidate_sha256": self._candidate,
                "policy_sha256": self._policy,
                "risk_class": risk_class,
                "lease_id": lease_id,
                "observed_at": _now(),
            },
        )
        return authorization

    def record_outcome(
        self,
        *,
        interaction_id: str,
        slice_id: str,
        success: bool,
        accurate: bool,
        escalated: bool,
        latency_ms: int,
    ) -> None:
        normalized_interaction = str(interaction_id or "").strip().lower()
        if _IDENTIFIER.fullmatch(normalized_interaction) is None:
            raise ValueError("canary_authorization_identity_invalid")
        if any(not isinstance(value, bool) for value in (success, accurate, escalated)):
            raise ValueError("canary_outcome_boolean_invalid")
        if isinstance(latency_ms, bool) or not isinstance(latency_ms, int) or latency_ms < 0:
            raise ValueError("canary_outcome_latency_invalid")
        interaction_id = normalized_interaction
        authorizations = [
            row
            for row in self._repository.read("canary_observations")
            if row.get("interaction_id") == interaction_id
            and row.get("admitted") is True
            and row.get("dataset_sha256") == self._dataset
            and row.get("candidate_sha256") == self._candidate
            and row.get("policy_sha256") == self._policy
        ]
        if not authorizations:
            raise ValueError("canary_authorization_missing")
        authorization = authorizations[-1]
        if self._clock().astimezone(UTC) >= self._expires_at or not self._leases.valid(
            lease_id=str(authorization["lease_id"]),
            fencing_token=int(authorization["fencing_token"]),
            policy_sha256=self._policy,
        ):
            raise ValueError("canary_lease_invalid")
        normalized_slice = str(slice_id or "").strip().lower()
        if not normalized_slice or len(normalized_slice) > 64:
            raise ValueError("canary_slice_invalid")
        self._repository.append_interaction_once(
            "canary_outcomes",
            {
                "interaction_id": interaction_id,
                "slice_id": normalized_slice,
                "success": bool(success),
                "accurate": bool(accurate),
                "escalated": bool(escalated),
                "latency_ms": latency_ms,
                "dataset_sha256": self._dataset,
                "candidate_sha256": self._candidate,
                "policy_sha256": self._policy,
                "fencing_token": int(authorization["fencing_token"]),
                "observed_at": _now(),
            },
        )

    def evidence(self) -> CanaryEvidence:
        rows = tuple(
            row
            for row in self._repository.read("canary_outcomes")
            if row.get("dataset_sha256") == self._dataset
            and row.get("candidate_sha256") == self._candidate
            and row.get("policy_sha256") == self._policy
        )
        if len(rows) < self._minimum_examples:
            raise ValueError("canary_minimum_examples_not_met")
        slice_rows: dict[str, list[Mapping[str, Any]]] = {}
        for row in rows:
            slice_rows.setdefault(str(row["slice_id"]), []).append(row)
        slice_metrics = {slice_id: _canary_metrics(values) for slice_id, values in sorted(slice_rows.items())}
        latencies = sorted(int(row["latency_ms"]) for row in rows)
        p95_index = max(0, (len(latencies) * 95 + 99) // 100 - 1)
        canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return CanaryEvidence(
            dataset_sha256=self._dataset,
            candidate_sha256=self._candidate,
            policy_sha256=self._policy,
            examples=len(rows),
            error_rate=sum(row["success"] is not True for row in rows) / len(rows),
            accuracy=sum(row["accurate"] is True for row in rows) / len(rows),
            escalation_rate=sum(row["escalated"] is True for row in rows) / len(rows),
            latency_p95_ms=latencies[p95_index],
            slice_metrics=slice_metrics,
            evidence_sha256=hashlib.sha256(canonical).hexdigest(),
        )


def _digest(value: str) -> str:
    normalized = str(value).strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("local_adapter_rollout_digest_invalid")
    return normalized


def _validate_evidence_binding(*values: str) -> None:
    for value in values:
        _digest(value)


def _nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"local_adapter_rollout_{name}_invalid")


def _rate(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"local_adapter_rollout_{name}_invalid")
    numeric = float(value)
    if not math.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
        raise ValueError(f"local_adapter_rollout_{name}_invalid")


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("canary_expiry_invalid")
    return parsed.astimezone(UTC)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canary_metrics(rows: list[Mapping[str, Any]]) -> Mapping[str, float | int]:
    return {
        "examples": len(rows),
        "error_rate": sum(row["success"] is not True for row in rows) / len(rows),
        "accuracy": sum(row["accurate"] is True for row in rows) / len(rows),
        "escalation_rate": sum(row["escalated"] is True for row in rows) / len(rows),
        "latency_max_ms": max(int(row["latency_ms"]) for row in rows),
    }


__all__ = [
    "CanaryAuthorization",
    "CanaryEvidence",
    "CanaryLeasePort",
    "LocalAdapterCanaryController",
    "LocalAdapterRolloutRepository",
    "LocalAdapterShadowController",
    "ShadowEvidence",
]
