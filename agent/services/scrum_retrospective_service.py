"""Evidence-bound retrospective, improvement and outcome services."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from agent.services.evolution.models import (
    EvolutionContext,
    EvolutionTrigger,
    EvolutionTriggerType,
)
from agent.services.scrum_state_store import ScrumStateStorePort

_PROPOSAL_TYPES = {"process", "prompt", "tool", "routing", "context", "test", "documentation"}
_PROTECTED_TARGET_PREFIXES = {"hub_core", "security_invariant", "task_queue_owner", "worker_orchestration"}


class RetrospectiveAnalysisPort(Protocol):
    def analyze(self, *, sprint_id: str, objective: str, signals: Mapping[str, Any]) -> list[dict[str, Any]]: ...


class SprintReadPort(Protocol):
    def require(self, sprint_id: str) -> dict[str, Any]: ...


class EvolutionRetrospectiveAnalysisAdapter:
    """Use the existing EvolutionEngine facade as an optional analysis provider."""

    def __init__(self, evolution_service: Any, *, provider_name: str | None = None) -> None:
        self._evolution = evolution_service
        self._provider_name = provider_name

    def analyze(self, *, sprint_id: str, objective: str, signals: Mapping[str, Any]) -> list[dict[str, Any]]:
        result = self._evolution.analyze(
            EvolutionContext(
                objective=objective,
                plan_id=sprint_id,
                signals=dict(signals),
                constraints={
                    "hub_owned": True,
                    "no_core_mutation": True,
                    "no_worker_orchestration": True,
                },
            ),
            provider_name=self._provider_name,
            trigger=EvolutionTrigger(
                trigger_type=EvolutionTriggerType.PERIODIC_REVIEW,
                source="scrum_retrospective",
                actor="hub",
                reason="sprint_retrospective",
            ),
            persist=False,
        )
        return [
            {
                "proposal_id": proposal.proposal_id,
                "title": _text(proposal.title, "evolution_title", maximum=500),
                "description": _text(proposal.description, "evolution_description", maximum=2000),
                "risk_level": _text(proposal.risk_level, "evolution_risk", maximum=64),
            }
            for proposal in result.proposals[:20]
        ]


class ScrumRetrospectiveService:
    """Turn bounded Sprint evidence into reviewed, measurable commitments."""

    def __init__(
        self,
        store: ScrumStateStorePort,
        sprints: SprintReadPort,
        *,
        analysis: RetrospectiveAnalysisPort | None = None,
    ) -> None:
        self._store = store
        self._sprints = sprints
        self._analysis = analysis

    def build_evidence_bundle(
        self,
        *,
        bundle_id: str,
        sprint_id: str,
        snapshot_ids: Sequence[str],
        artifact_refs: Sequence[str],
        audit_refs: Sequence[str],
        delivery_metrics: Mapping[str, float],
        process_signals: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        sprint = self._sprints.require(sprint_id)
        if sprint["lifecycle_state"] != "retrospective":
            raise ValueError("retrospective_sprint_state_invalid")
        snapshots = [self._store.get("sprint_snapshot", value) for value in snapshot_ids]
        if not snapshots or any(value is None or value["sprint_id"] != sprint_id for value in snapshots):
            raise ValueError("retrospective_snapshot_invalid")
        signals = [_process_signal(value) for value in process_signals]
        if not signals:
            raise ValueError("retrospective_process_signals_invalid")
        payload = {
            "schema": "ananta.retrospective-evidence-bundle.v1",
            "scope_id": sprint["scope_id"],
            "bundle_id": _text(bundle_id, "bundle_id"),
            "sprint_id": sprint_id,
            "sprint_revision": sprint["revision"],
            "sprint_goal": sprint["sprint_goal"],
            "snapshot_ids": _tokens(snapshot_ids, "snapshot_id"),
            "artifact_refs": _tokens(artifact_refs, "artifact_ref"),
            "audit_refs": _tokens(audit_refs, "audit_ref"),
            "delivery_metrics": _metrics(delivery_metrics),
            "process_signals": signals,
            "architecture_revision_id": sprint["architecture_handoff"]["architecture_revision_id"],
            "architecture_finding_ids": sorted(
                {
                    finding
                    for snapshot in snapshots
                    for finding in snapshot["architecture_finding_ids"]
                }
            ),
            "raw_prompts_included": False,
        }
        return self._store.append("retrospective_bundle", bundle_id, payload, expected_revision=0)

    def analyze(
        self,
        *,
        retrospective_id: str,
        bundle_id: str,
        perspectives: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        bundle = self._require("retrospective_bundle", bundle_id, "retrospective_bundle_unknown")
        normalized = [_perspective(value) for value in perspectives]
        roles = {value["role"] for value in normalized}
        if not {"product_owner", "scrum_master", "developer"}.issubset(roles):
            raise ValueError("retrospective_perspectives_incomplete")
        hypotheses = []
        for signal in bundle["process_signals"]:
            related = [
                value
                for value in normalized
                if signal["signal_id"] in value["supported_signal_ids"]
            ]
            if not related:
                continue
            hypotheses.append(
                {
                    "hypothesis_id": _stable_id(retrospective_id, str(signal["signal_id"])),
                    "statement": str(signal["summary"])[:1000],
                    "evidence_signal_ids": [str(signal["signal_id"])],
                    "supporting_roles": sorted(value["role"] for value in related if value["stance"] == "support"),
                    "challenging_roles": sorted(value["role"] for value in related if value["stance"] == "challenge"),
                    "causal_claim_made": False,
                }
            )
        external_proposals: list[dict[str, Any]] = []
        analysis_status = "deterministic_only"
        if self._analysis is not None:
            try:
                external_proposals = self._analysis.analyze(
                    sprint_id=bundle["sprint_id"],
                    objective="Propose bounded Scrum process improvements from supplied evidence.",
                    signals={
                        "bundle_id": bundle_id,
                        "delivery_metrics": bundle["delivery_metrics"],
                        "process_signals": bundle["process_signals"],
                        "perspectives": normalized,
                    },
                )
            except Exception as exc:
                analysis_status = f"provider_unavailable:{type(exc).__name__}"
            else:
                analysis_status = "evolution_engine_completed"
        payload = {
            "schema": "ananta.scrum-retrospective-analysis.v1",
            "scope_id": bundle["scope_id"],
            "retrospective_id": _text(retrospective_id, "retrospective_id"),
            "bundle_id": bundle_id,
            "sprint_id": bundle["sprint_id"],
            "perspectives": normalized,
            "hypotheses": hypotheses,
            "external_proposals": external_proposals,
            "analysis_status": analysis_status,
            "dissent_preserved": any(value["challenging_roles"] for value in hypotheses),
        }
        return self._store.append("retrospective", retrospective_id, payload, expected_revision=0)

    def propose_improvement(
        self,
        *,
        proposal_id: str,
        retrospective_id: str,
        hypothesis_ids: Sequence[str],
        proposal_type: str,
        target_ref: str,
        description: str,
        expected_effect: str,
        risk_level: str,
        experiment: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        retrospective = self._require("retrospective", retrospective_id, "retrospective_unknown")
        known = {value["hypothesis_id"] for value in retrospective["hypotheses"]}
        hypotheses = _tokens(hypothesis_ids, "hypothesis_id")
        if not hypotheses or set(hypotheses).difference(known):
            raise ValueError("improvement_hypothesis_invalid")
        normalized_type = str(proposal_type or "").strip()
        if normalized_type not in _PROPOSAL_TYPES:
            raise ValueError("improvement_proposal_type_invalid")
        risk = str(risk_level or "").strip()
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("improvement_risk_invalid")
        target = _text(target_ref, "target_ref", maximum=1000)
        protected = any(target.startswith(value) for value in _PROTECTED_TARGET_PREFIXES)
        payload = {
            "schema": "ananta.scrum-improvement-proposal.v1",
            "scope_id": retrospective["scope_id"],
            "proposal_id": _text(proposal_id, "proposal_id"),
            "retrospective_id": retrospective_id,
            "sprint_id": retrospective["sprint_id"],
            "hypothesis_ids": hypotheses,
            "proposal_type": normalized_type,
            "target_ref": target,
            "description": _text(description, "description", maximum=4000),
            "expected_effect": _text(expected_effect, "expected_effect", maximum=2000),
            "risk_level": risk,
            "experiment": _bounded_mapping(experiment or {}, limit=8192),
            "protected_target": protected,
            "status": "proposed",
            "review": None,
        }
        return self._store.append("improvement_proposal", proposal_id, payload, expected_revision=0)

    def review_improvement(
        self,
        *,
        proposal_id: str,
        reviewer_id: str,
        checks: Mapping[str, bool],
    ) -> dict[str, Any]:
        proposal = self._require("improvement_proposal", proposal_id, "improvement_proposal_unknown")
        required = {"evidence", "scope", "security", "rollback", "measurable"}
        normalized = {str(key): bool(value) for key, value in checks.items()}
        passed = set(normalized) == required and all(normalized.values())
        if proposal["protected_target"]:
            status = "rejected_protected_target"
        elif proposal["risk_level"] in {"high", "critical"}:
            status = "rejected_risk_requires_separate_engineering_change"
        elif passed:
            status = "accepted"
        else:
            status = "rejected_checks"
        return self._store.append(
            "improvement_proposal",
            proposal_id,
            {
                **proposal,
                "status": status,
                "review": {
                    "reviewer_id": _text(reviewer_id, "reviewer_id"),
                    "automated": True,
                    "checks": normalized,
                },
            },
            expected_revision=proposal["revision"],
        )

    def create_commitment(
        self,
        *,
        commitment_id: str,
        proposal_id: str,
        owner_role: str,
        metric_names: Sequence[str],
        rollback_rule: str,
    ) -> dict[str, Any]:
        proposal = self._require("improvement_proposal", proposal_id, "improvement_proposal_unknown")
        if proposal["status"] != "accepted":
            raise ValueError("improvement_proposal_not_accepted")
        payload = {
            "schema": "ananta.scrum-improvement-commitment.v1",
            "scope_id": proposal["scope_id"],
            "commitment_id": _text(commitment_id, "commitment_id"),
            "proposal_id": proposal_id,
            "source_sprint_id": proposal["sprint_id"],
            "owner_role": _text(owner_role, "owner_role"),
            "metric_names": _tokens(metric_names, "metric_name"),
            "rollback_rule": _text(rollback_rule, "rollback_rule", maximum=2000),
            "status": "accepted",
        }
        if not payload["metric_names"]:
            raise ValueError("improvement_metrics_required")
        return self._store.append("improvement_commitment", commitment_id, payload, expected_revision=0)

    def experiment_assignment(
        self,
        *,
        commitment_id: str,
        sprint_id: str,
        subject_id: str,
        treatment_basis_points: int,
    ) -> dict[str, Any]:
        self._require("improvement_commitment", commitment_id, "improvement_commitment_unknown")
        sprint = self._sprints.require(sprint_id)
        if commitment_id not in sprint["improvement_commitment_ids"] or not 1 <= treatment_basis_points <= 9999:
            raise ValueError("improvement_experiment_invalid")
        bucket = int.from_bytes(
            hashlib.sha256(f"{commitment_id}\0{sprint_id}\0{subject_id}".encode()).digest()[:8], "big"
        ) % 10_000
        return {
            "schema": "ananta.scrum-improvement-experiment-assignment.v1",
            "commitment_id": commitment_id,
            "sprint_id": sprint_id,
            "subject_id": _text(subject_id, "subject_id"),
            "variant": "treatment" if bucket < treatment_basis_points else "control",
            "bucket": bucket,
        }

    def evaluate_commitment(
        self,
        *,
        evaluation_id: str,
        commitment_id: str,
        sprint_id: str,
        baseline_metrics: Mapping[str, float],
        observed_metrics: Mapping[str, float],
        sample_size: int,
    ) -> dict[str, Any]:
        commitment = self._require("improvement_commitment", commitment_id, "improvement_commitment_unknown")
        sprint = self._sprints.require(sprint_id)
        if commitment_id not in sprint["improvement_commitment_ids"] or sprint["lifecycle_state"] != "closed":
            raise ValueError("improvement_commitment_not_bound")
        baseline = _metrics(baseline_metrics)
        observed = _metrics(observed_metrics)
        if set(baseline) != set(commitment["metric_names"]) or set(observed) != set(baseline) or sample_size < 1:
            raise ValueError("improvement_evaluation_metrics_invalid")
        deltas = {key: observed[key] - baseline[key] for key in baseline}
        if sample_size < 3:
            outcome = "inconclusive"
        elif all(value <= 0 for value in deltas.values()) and any(value < 0 for value in deltas.values()):
            outcome = "improved"
        elif any(value > 0 for value in deltas.values()):
            outcome = "regressed"
        else:
            outcome = "neutral"
        payload = {
            "schema": "ananta.scrum-improvement-effect.v1",
            "scope_id": commitment["scope_id"],
            "evaluation_id": _text(evaluation_id, "evaluation_id"),
            "commitment_id": commitment_id,
            "sprint_id": sprint_id,
            "baseline_metrics": baseline,
            "observed_metrics": observed,
            "deltas": deltas,
            "sample_size": sample_size,
            "outcome": outcome,
            "rollback_automatic": outcome == "regressed",
        }
        result = self._store.append("improvement_effect", evaluation_id, payload, expected_revision=0)
        if outcome == "regressed":
            self._store.append(
                "improvement_commitment",
                commitment_id,
                {**commitment, "status": "rolled_back", "rollback_evaluation_id": evaluation_id},
                expected_revision=commitment["revision"],
            )
        return result

    def _require(self, kind: str, entity_id: str, reason: str) -> dict[str, Any]:
        value = self._store.get(kind, entity_id)
        if value is None:
            raise ValueError(reason)
        return value


def _perspective(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(value)
    if set(candidate) != {"role", "stance", "summary", "supported_signal_ids", "alternative_causes"}:
        raise ValueError("retrospective_perspective_invalid")
    role = str(candidate["role"] or "").strip()
    if role not in {"product_owner", "scrum_master", "developer", "architecture_governance"}:
        raise ValueError("retrospective_role_invalid")
    stance = str(candidate["stance"] or "").strip()
    if stance not in {"support", "challenge", "neutral"}:
        raise ValueError("retrospective_stance_invalid")
    return {
        "role": role,
        "stance": stance,
        "summary": _text(candidate["summary"], "perspective_summary", maximum=2000),
        "supported_signal_ids": _tokens(candidate["supported_signal_ids"], "signal_id"),
        "alternative_causes": _tokens(candidate["alternative_causes"], "alternative_cause", maximum=1000),
    }


def _process_signal(value: Mapping[str, Any]) -> dict[str, Any]:
    candidate = dict(value)
    allowed = {"signal_id", "summary", "category", "count", "artifact_refs"}
    if not {"signal_id", "summary"}.issubset(candidate) or set(candidate).difference(allowed):
        raise ValueError("retrospective_process_signals_invalid")
    count = candidate.get("count", 1)
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 1_000_000:
        raise ValueError("retrospective_process_signals_invalid")
    return {
        "signal_id": _text(candidate["signal_id"], "signal_id"),
        "summary": _text(candidate["summary"], "signal_summary", maximum=2000),
        "category": _text(candidate.get("category", "process"), "signal_category"),
        "count": count,
        "artifact_refs": _tokens(candidate.get("artifact_refs", ()), "artifact_ref"),
    }


def _metrics(value: Mapping[str, float]) -> dict[str, float]:
    result: dict[str, float] = {}
    for key, item in value.items():
        normalized = float(item)
        if not str(key).strip() or not math.isfinite(normalized) or normalized < 0:
            raise ValueError("retrospective_metrics_invalid")
        result[str(key)] = normalized
    if not result or len(result) > 100:
        raise ValueError("retrospective_metrics_invalid")
    return result


def _bounded_mapping(value: Mapping[str, Any], *, limit: int) -> dict[str, Any]:
    rendered = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(rendered.encode()) > limit:
        raise ValueError("retrospective_payload_too_large")
    return json.loads(rendered)


def _text(value: object, field: str, *, maximum: int = 256) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > maximum or "\0" in normalized:
        raise ValueError(f"retrospective_{field}_invalid")
    return normalized


def _tokens(values: Sequence[object], field: str, *, maximum: int = 256) -> list[str]:
    result = [_text(value, field, maximum=maximum) for value in values]
    if len(result) != len(set(result)) or len(result) > 1000:
        raise ValueError(f"retrospective_{field}_invalid")
    return result


def _stable_id(namespace: str, value: str) -> str:
    return hashlib.sha256(f"{namespace}\0{value}".encode()).hexdigest()[:24]


__all__ = [
    "EvolutionRetrospectiveAnalysisAdapter",
    "RetrospectiveAnalysisPort",
    "ScrumRetrospectiveService",
    "SprintReadPort",
]
