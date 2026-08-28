"""Hub policy for versioned architecture baselines and delivery feedback."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.scrum_state_store import ScrumStateStorePort

_EVIDENCE_TYPES = {
    "adr_assumption",
    "integration_failure",
    "performance",
    "security",
    "data_contract",
    "api_contract",
    "reliability",
    "architecture_debt",
}
_OUTCOMES = {"improved", "neutral", "regressed", "inconclusive"}


class ScrumArchitectureLoopService:
    """Maintain cross-sprint architecture state without owning backlog priority."""

    def __init__(self, store: ScrumStateStorePort) -> None:
        self._store = store

    def create_baseline(
        self,
        *,
        scope_id: str,
        revision_id: str,
        author_id: str,
        parent_revision_id: str | None,
        target_architecture: Mapping[str, Any],
        guardrails: Sequence[Mapping[str, Any]],
        adr_refs: Sequence[str],
        known_debt_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        if self._store.get("architecture_baseline", revision_id):
            raise ValueError("architecture_baseline_revision_exists")
        if parent_revision_id:
            parent = self.require_baseline(parent_revision_id)
            if parent["scope_id"] != scope_id:
                raise ValueError("architecture_baseline_parent_scope_mismatch")
        normalized_guardrails = [_guardrail(value) for value in guardrails]
        if len({value["guardrail_id"] for value in normalized_guardrails}) != len(normalized_guardrails):
            raise ValueError("architecture_guardrail_invalid")
        for debt_id in known_debt_ids:
            debt = self._store.get("architecture_debt", str(debt_id))
            if debt is None or debt["scope_id"] != scope_id:
                raise ValueError("architecture_debt_unknown")
        payload = {
            "schema": "ananta.architecture-baseline.v1",
            "scope_id": _required(scope_id, "scope"),
            "revision_id": _required(revision_id, "revision"),
            "parent_revision_id": str(parent_revision_id or ""),
            "author_id": _required(author_id, "author"),
            "lifecycle_state": "draft",
            "target_architecture": _bounded_mapping(target_architecture),
            "guardrails": normalized_guardrails,
            "guardrail_digest": _digest(normalized_guardrails),
            "adr_refs": _tokens(adr_refs, "adr_ref"),
            "known_debt_ids": _tokens(known_debt_ids, "debt_id"),
            "review": None,
        }
        return self._store.append("architecture_baseline", revision_id, payload, expected_revision=0)

    def activate_baseline(
        self,
        *,
        revision_id: str,
        reviewer_id: str,
        checks: Mapping[str, bool],
        evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        baseline = self.require_baseline(revision_id)
        if baseline["lifecycle_state"] == "active":
            return baseline
        required_checks = {"scope", "security", "compatibility", "migration", "evidence"}
        normalized_checks = {str(key): bool(value) for key, value in checks.items()}
        if set(normalized_checks) != required_checks or not all(normalized_checks.values()):
            raise ValueError("architecture_baseline_review_failed")
        reviewer = _required(reviewer_id, "reviewer")
        if reviewer == baseline["author_id"]:
            raise ValueError("architecture_baseline_independent_review_required")
        if not evidence_refs:
            raise ValueError("architecture_baseline_evidence_required")
        for active in self._store.list("architecture_baseline", scope_id=baseline["scope_id"]):
            if active["lifecycle_state"] == "active" and active["revision_id"] != revision_id:
                self._store.append(
                    "architecture_baseline",
                    active["entity_id"],
                    {**active, "lifecycle_state": "superseded"},
                    expected_revision=active["revision"],
                )
        return self._store.append(
            "architecture_baseline",
            revision_id,
            {
                **baseline,
                "lifecycle_state": "active",
                "review": {
                    "reviewer_id": reviewer,
                    "automated": True,
                    "checks": normalized_checks,
                    "evidence_refs": _tokens(evidence_refs, "evidence_ref"),
                },
            },
            expected_revision=baseline["revision"],
        )

    def handoff(self, *, scope_id: str, sprint_scope: Sequence[str]) -> dict[str, Any]:
        candidates = [
            item
            for item in self._store.list("architecture_baseline", scope_id=scope_id)
            if item["lifecycle_state"] == "active"
        ]
        if len(candidates) != 1:
            raise ValueError("architecture_active_baseline_unavailable")
        baseline = candidates[0]
        requested_scope = set(_tokens(sprint_scope, "sprint_scope"))
        projected = [
            item
            for item in baseline["guardrails"]
            if not item["scopes"] or requested_scope.intersection(item["scopes"])
        ]
        return {
            "schema": "ananta.architecture-sprint-handoff.v1",
            "scope_id": scope_id,
            "architecture_revision_id": baseline["revision_id"],
            "architecture_revision_number": baseline["revision"],
            "guardrails": projected,
            "guardrail_digest": _digest(projected),
            "adr_refs": list(baseline["adr_refs"]),
        }

    def record_delivery_evidence(
        self,
        *,
        evidence_id: str,
        scope_id: str,
        sprint_id: str,
        architecture_revision_id: str,
        evidence_type: str,
        severity: str,
        artifact_refs: Sequence[str],
        summary: str,
    ) -> dict[str, Any]:
        baseline = self.require_baseline(architecture_revision_id)
        if baseline["scope_id"] != scope_id:
            raise ValueError("architecture_evidence_scope_mismatch")
        normalized_type = str(evidence_type or "").strip()
        if normalized_type not in _EVIDENCE_TYPES:
            raise ValueError("architecture_evidence_type_invalid")
        normalized_severity = str(severity or "").strip()
        if normalized_severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("architecture_evidence_severity_invalid")
        if normalized_severity == "low" and normalized_type not in {"architecture_debt", "adr_assumption"}:
            raise ValueError("architecture_evidence_below_relevance_threshold")
        payload = {
            "schema": "ananta.sprint-architecture-evidence.v1",
            "scope_id": scope_id,
            "evidence_id": _required(evidence_id, "evidence_id"),
            "sprint_id": _required(sprint_id, "sprint_id"),
            "architecture_revision_id": architecture_revision_id,
            "evidence_type": normalized_type,
            "severity": normalized_severity,
            "artifact_refs": _tokens(artifact_refs, "artifact_ref"),
            "summary": _required(summary, "evidence_summary", maximum=2000),
        }
        return self._store.append("architecture_evidence", evidence_id, payload, expected_revision=0)

    def register_debt(
        self,
        *,
        debt_id: str,
        scope_id: str,
        cause: str,
        risk: str,
        evidence_ids: Sequence[str],
        workaround: str,
        expected_effect: str,
    ) -> dict[str, Any]:
        evidence = [self._store.get("architecture_evidence", value) for value in evidence_ids]
        if not evidence or any(item is None or item["scope_id"] != scope_id for item in evidence):
            raise ValueError("architecture_debt_evidence_unavailable")
        payload = {
            "schema": "ananta.architecture-debt.v1",
            "scope_id": _required(scope_id, "scope"),
            "debt_id": _required(debt_id, "debt_id"),
            "cause": _required(cause, "debt_cause", maximum=2000),
            "risk": _required(risk, "debt_risk", maximum=1000),
            "evidence_ids": _tokens(evidence_ids, "evidence_id"),
            "workaround": _required(workaround, "debt_workaround", maximum=2000),
            "expected_effect": _required(expected_effect, "debt_effect", maximum=2000),
            "status": "open",
        }
        return self._store.append("architecture_debt", debt_id, payload, expected_revision=0)

    def propose_change(
        self,
        *,
        proposal_id: str,
        scope_id: str,
        parent_revision_id: str,
        author_id: str,
        evidence_ids: Sequence[str],
        affected_guardrails: Sequence[str],
        alternatives: Sequence[str],
        tradeoffs: Sequence[str],
        migration: Mapping[str, Any],
    ) -> dict[str, Any]:
        parent = self.require_baseline(parent_revision_id)
        if parent["scope_id"] != scope_id or parent["lifecycle_state"] != "active":
            raise ValueError("architecture_change_parent_not_active")
        evidence = [self._store.get("architecture_evidence", value) for value in evidence_ids]
        if not evidence or any(item is None or item["scope_id"] != scope_id for item in evidence):
            raise ValueError("architecture_change_evidence_invalid")
        migration_payload = _bounded_mapping(migration)
        if set(migration_payload) != {"breaking", "strategy", "compatibility"}:
            raise ValueError("architecture_change_migration_invalid")
        payload = {
            "schema": "ananta.architecture-change-proposal.v1",
            "scope_id": scope_id,
            "proposal_id": _required(proposal_id, "proposal_id"),
            "parent_revision_id": parent_revision_id,
            "author_id": _required(author_id, "author"),
            "evidence_ids": _tokens(evidence_ids, "evidence_id"),
            "affected_guardrails": _tokens(affected_guardrails, "guardrail_id"),
            "alternatives": _tokens(alternatives, "alternative", maximum=2000),
            "tradeoffs": _tokens(tradeoffs, "tradeoff", maximum=2000),
            "migration": migration_payload,
            "status": "proposed",
            "review": None,
        }
        return self._store.append("architecture_change", proposal_id, payload, expected_revision=0)

    def review_change(
        self,
        *,
        proposal_id: str,
        reviewer_id: str,
        checks: Mapping[str, bool],
        decision: str,
    ) -> dict[str, Any]:
        proposal = self._require("architecture_change", proposal_id, "architecture_change_unknown")
        reviewer = _required(reviewer_id, "reviewer")
        if reviewer == proposal["author_id"]:
            raise ValueError("architecture_change_independent_review_required")
        normalized_decision = str(decision or "").strip()
        if normalized_decision not in {"accepted", "rejected"}:
            raise ValueError("architecture_change_decision_invalid")
        required = {"evidence", "security", "compatibility", "migration", "scope"}
        normalized_checks = {str(key): bool(value) for key, value in checks.items()}
        accepted = (
            normalized_decision == "accepted"
            and set(normalized_checks) == required
            and all(normalized_checks.values())
        )
        status = "accepted" if accepted else "rejected"
        return self._store.append(
            "architecture_change",
            proposal_id,
            {
                **proposal,
                "status": status,
                "review": {"reviewer_id": reviewer, "automated": True, "checks": normalized_checks},
            },
            expected_revision=proposal["revision"],
        )

    def materialize_accepted_change(
        self,
        *,
        proposal_id: str,
        revision_id: str,
        target_architecture: Mapping[str, Any],
        guardrails: Sequence[Mapping[str, Any]],
        adr_refs: Sequence[str],
        known_debt_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        proposal = self._require("architecture_change", proposal_id, "architecture_change_unknown")
        if proposal["status"] != "accepted":
            raise ValueError("architecture_change_not_accepted")
        baseline = self.create_baseline(
            scope_id=proposal["scope_id"],
            revision_id=revision_id,
            author_id=f"proposal:{proposal_id}",
            parent_revision_id=proposal["parent_revision_id"],
            target_architecture=target_architecture,
            guardrails=guardrails,
            adr_refs=adr_refs,
            known_debt_ids=known_debt_ids,
        )
        return self._store.append(
            "architecture_change",
            proposal_id,
            {**proposal, "materialized_revision_id": revision_id, "status": "materialized"},
            expected_revision=proposal["revision"],
        ) | {"baseline": baseline}

    def evaluate_revision_effect(
        self,
        *,
        evaluation_id: str,
        scope_id: str,
        revision_id: str,
        baseline_metrics: Mapping[str, float],
        observed_metrics: Mapping[str, float],
        sample_size: int,
    ) -> dict[str, Any]:
        revision = self.require_baseline(revision_id)
        if revision["scope_id"] != scope_id:
            raise ValueError("architecture_effect_scope_mismatch")
        baseline = _metrics(baseline_metrics)
        observed = _metrics(observed_metrics)
        if set(baseline) != set(observed) or sample_size < 1:
            raise ValueError("architecture_effect_metrics_invalid")
        deltas = {key: observed[key] - baseline[key] for key in baseline}
        if sample_size < 3:
            outcome = "inconclusive"
        elif all(value <= 0 for value in deltas.values()) and any(value < 0 for value in deltas.values()):
            outcome = "improved"
        elif any(value > 0 for value in deltas.values()):
            outcome = "regressed"
        else:
            outcome = "neutral"
        assert outcome in _OUTCOMES
        payload = {
            "schema": "ananta.architecture-effect-evaluation.v1",
            "scope_id": scope_id,
            "evaluation_id": _required(evaluation_id, "evaluation_id"),
            "revision_id": revision_id,
            "baseline_metrics": baseline,
            "observed_metrics": observed,
            "deltas": deltas,
            "sample_size": sample_size,
            "outcome": outcome,
            "follow_up_required": outcome == "regressed",
        }
        return self._store.append("architecture_effect", evaluation_id, payload, expected_revision=0)

    def require_baseline(self, revision_id: str) -> dict[str, Any]:
        return self._require("architecture_baseline", revision_id, "architecture_baseline_unknown")

    def _require(self, kind: str, entity_id: str, reason: str) -> dict[str, Any]:
        value = self._store.get(kind, entity_id)
        if value is None:
            raise ValueError(reason)
        return value


def _guardrail(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(value)
    if set(candidate) != {"guardrail_id", "rule", "scopes"}:
        raise ValueError("architecture_guardrail_invalid")
    return {
        "guardrail_id": _required(candidate["guardrail_id"], "guardrail_id"),
        "rule": _required(candidate["rule"], "guardrail_rule", maximum=2000),
        "scopes": _tokens(candidate["scopes"], "guardrail_scope"),
    }


def _metrics(value: Mapping[str, float]) -> dict[str, float]:
    allowed = {
        "defects",
        "rework",
        "integration_failures",
        "latency",
        "reliability_loss",
        "security_findings",
        "change_cost",
        "debt",
    }
    result = {str(key): float(item) for key, item in value.items() if str(key) in allowed}
    if (
        not result
        or len(result) != len(value)
        or any(not math.isfinite(item) or item < 0 for item in result.values())
    ):
        raise ValueError("architecture_effect_metrics_invalid")
    return result


def _required(value: object, field: str, *, maximum: int = 256) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum or "\0" in normalized:
        raise ValueError(f"architecture_{field}_invalid")
    return normalized


def _tokens(values: Sequence[object], field: str, *, maximum: int = 256) -> list[str]:
    result = [_required(value, field, maximum=maximum) for value in values]
    if len(result) != len(set(result)) or len(result) > 500:
        raise ValueError(f"architecture_{field}_invalid")
    return result


def _bounded_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    rendered = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(rendered.encode()) > 64 * 1024:
        raise ValueError("architecture_payload_too_large")
    return json.loads(rendered)


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


__all__ = ["ScrumArchitectureLoopService"]
