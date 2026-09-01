"""Deterministic, advisory-only review of composite task-chain risks."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agent.composite_risk_review_contract import (
    COMPOSITE_RISK_REVIEW_SCHEMA,
    COMPOSITE_RISK_REVIEW_WARNING,
)

_SECURITY_PATH_TERMS = {
    "auth",
    "credential",
    "crypto",
    "firewall",
    "oauth",
    "permission",
    "secret",
    "security",
    "token",
}
_CAPABILITY_TERMS = {
    "auth": {"auth", "credential", "login", "oauth", "password", "token"},
    "network": {"api", "dns", "http", "network", "proxy", "socket", "webhook"},
    "payload": {"artifact", "binary", "payload", "script", "template"},
    "deploy": {"container", "deploy", "publish", "release", "rollout"},
}
_ASSEMBLY_TERMS = {"assemble", "assembly", "bundle", "combine", "integrate", "package", "release"}


@dataclass(frozen=True, slots=True)
class RiskIndicator:
    id: str
    description: str
    severity: str
    matched_evidence: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "severity": self.severity,
            "matched_evidence": [dict(item) for item in self.matched_evidence],
        }


@dataclass(frozen=True, slots=True)
class ReviewContext:
    goal_text: str
    tasks: tuple[dict[str, Any], ...]
    artifacts: tuple[dict[str, Any], ...]
    audit_events: tuple[dict[str, Any], ...]


class CompositeRiskRule(Protocol):
    def __call__(self, context: ReviewContext) -> RiskIndicator | None: ...


class CompositeRiskReviewService:
    """Apply bounded explainable rules without making an allow/deny decision."""

    def __init__(self, rules: Sequence[CompositeRiskRule] | None = None) -> None:
        self._rules = tuple(
            rules
            or (
                self._security_files,
                self._capability_chain,
                self._scope_shift,
                self._final_assembly,
            )
        )

    def review(
        self,
        *,
        goal: object = None,
        tasks: Sequence[object] | None = None,
        artifacts_metadata: Sequence[object] | None = None,
        audit_events: Sequence[object] | None = None,
    ) -> dict[str, Any]:
        normalized_tasks = self._records(tasks, limit=1000)
        artifacts = self._records(artifacts_metadata, limit=2000)
        events = self._records(audit_events, limit=2000)
        goal_text = self._text(goal)
        if not goal_text and not normalized_tasks and not artifacts and not events:
            return self._result(
                risk_level="insufficient_context",
                indicators=[],
                explanation="Fuer einen Review wurden keine auswertbaren Kontextdaten bereitgestellt.",
                recommended_action="collect_more_context",
            )

        context = ReviewContext(
            goal_text=goal_text,
            tasks=tuple(normalized_tasks),
            artifacts=tuple(artifacts),
            audit_events=tuple(events),
        )
        indicators = [indicator for rule in self._rules if (indicator := rule(context)) is not None]
        risk_level = self._risk_level(indicators)
        recommended = {
            "low": "log_review_hint",
            "medium": "automated_independent_review",
            "high": "automated_policy_escalation",
        }[risk_level]
        explanation = (
            f"{len(indicators)} erklaerbare Risiko-Indikator(en) wurden gefunden."
            if indicators
            else "Die begrenzten Regeln fanden keinen Indikator; dies ist keine Sicherheitsfreigabe."
        )
        return self._result(
            risk_level=risk_level,
            indicators=indicators,
            explanation=explanation,
            recommended_action=recommended,
        )

    @staticmethod
    def _records(values: Sequence[object] | None, *, limit: int) -> list[dict[str, Any]]:
        if not isinstance(values, (list, tuple)):
            return []
        rows: list[dict[str, Any]] = []
        for index, value in enumerate(values[:limit]):
            if isinstance(value, Mapping):
                rows.append(dict(value))
            elif isinstance(value, str) and value.strip():
                rows.append({"id": f"item-{index + 1}", "text": value.strip()})
        return rows

    @staticmethod
    def _text(value: object) -> str:
        if isinstance(value, str):
            return value.lower()[:100_000]
        if isinstance(value, Mapping):
            parts = [
                str(value.get(key) or "")
                for key in (
                    "id",
                    "title",
                    "name",
                    "description",
                    "text",
                    "path",
                    "scope",
                    "domain",
                    "kind",
                    "action",
                    "event_type",
                    "tool_name",
                )
            ]
            return " ".join(parts).lower()[:100_000]
        return ""

    def _security_files(self, context: ReviewContext) -> RiskIndicator | None:
        matches = []
        for index, artifact in enumerate(context.artifacts):
            path = str(artifact.get("path") or artifact.get("name") or "").strip()
            lowered = path.lower()
            terms = sorted(term for term in _SECURITY_PATH_TERMS if term in lowered)
            if terms:
                matches.append(
                    {
                        "artifact_ref": str(artifact.get("id") or path or f"artifact-{index + 1}"),
                        "matched_terms": terms,
                    }
                )
        if len(matches) < 3:
            return None
        return RiskIndicator(
            "many_security_relevant_files",
            "Mindestens drei sicherheitsrelevante Artefaktpfade treten in derselben Kette auf.",
            "medium",
            tuple(matches[:20]),
        )

    def _capability_chain(self, context: ReviewContext) -> RiskIndicator | None:
        records = [*context.tasks, *context.artifacts, *context.audit_events]
        texts = [context.goal_text, *(self._text(item) for item in records)]
        evidence = []
        for capability, terms in _CAPABILITY_TERMS.items():
            matched = sorted({term for text in texts for term in terms if term in text})
            if matched:
                evidence.append({"capability": capability, "matched_terms": matched})
        if len(evidence) != len(_CAPABILITY_TERMS):
            return None
        return RiskIndicator(
            "auth_network_payload_deploy_chain",
            "Auth-, Netzwerk-, Payload- und Deployment-Faehigkeiten kommen gemeinsam vor.",
            "high",
            tuple(evidence),
        )

    def _scope_shift(self, context: ReviewContext) -> RiskIndicator | None:
        tasks = context.tasks
        if len(tasks) < 3:
            return None
        scopes = [
            str(task.get("scope") or task.get("domain") or task.get("kind") or "").strip().lower()
            for task in tasks
        ]
        previous = [scope for scope in scopes[:-1] if scope]
        final = scopes[-1]
        if not final or len(previous) < 2:
            return None
        dominant, count = Counter(previous).most_common(1)[0]
        if final == dominant or count < 2:
            return None
        return RiskIndicator(
            "sudden_scope_shift",
            "Der letzte Task wechselt aus einem zuvor wiederholten Scope.",
            "medium",
            (
                {
                    "previous_scope": dominant,
                    "previous_count": count,
                    "final_scope": final,
                    "final_task_ref": str(tasks[-1].get("id") or f"task-{len(tasks)}"),
                },
            ),
        )

    def _final_assembly(self, context: ReviewContext) -> RiskIndicator | None:
        tasks = context.tasks
        artifacts = context.artifacts
        if not tasks or len(artifacts) < 3:
            return None
        final_text = self._text(tasks[-1])
        matched = sorted(term for term in _ASSEMBLY_TERMS if term in final_text)
        if not matched:
            return None
        return RiskIndicator(
            "final_assembly_after_many_artifacts",
            "Ein abschliessender Assembly-Schritt folgt auf mehrere Teilartefakte.",
            "high",
            (
                {
                    "final_task_ref": str(tasks[-1].get("id") or f"task-{len(tasks)}"),
                    "artifact_count": len(artifacts),
                    "matched_terms": matched,
                },
            ),
        )

    @staticmethod
    def _risk_level(indicators: list[RiskIndicator]) -> str:
        if any(indicator.severity == "high" for indicator in indicators) or len(indicators) >= 3:
            return "high"
        if indicators:
            return "medium"
        return "low"

    @staticmethod
    def _result(
        *,
        risk_level: str,
        indicators: list[RiskIndicator],
        explanation: str,
        recommended_action: str,
    ) -> dict[str, Any]:
        return {
            "schema": COMPOSITE_RISK_REVIEW_SCHEMA,
            "review_only": True,
            "risk_level": risk_level,
            "indicators": [indicator.as_dict() for indicator in indicators],
            "explanation": explanation,
            "recommended_action": recommended_action,
            "warning_text": COMPOSITE_RISK_REVIEW_WARNING,
        }


composite_risk_review_service = CompositeRiskReviewService()


def get_composite_risk_review_service() -> CompositeRiskReviewService:
    return composite_risk_review_service
