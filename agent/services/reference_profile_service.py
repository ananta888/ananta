from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReferenceSource:
    repo: str
    path_hint: str
    rationale: str


@dataclass(frozen=True)
class ReferenceProvenance:
    owner: str
    captured_at: str
    curation_note: str


@dataclass(frozen=True)
class ReferenceProfile:
    profile_id: str
    language: str
    framework: str
    project_type: str
    reference_source: ReferenceSource
    strengths: list[str]
    limitations: list[str]
    intended_usage: str
    supported_flows: list[str]
    provenance: ReferenceProvenance

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["model_fields"] = [
            "language",
            "framework",
            "project_type",
            "reference_source",
            "strengths",
            "limitations",
            "intended_usage",
        ]
        return payload


REFERENCE_USAGE_BOUNDARY: dict[str, Any] = {
    "mode": "guidance_not_clone",
    "intended_reuse": [
        "project structure",
        "module layering",
        "configuration conventions",
        "test patterns",
        "api layout",
        "security conventions",
    ],
    "forbidden_reuse": [
        "blind file copy",
        "unbounded repository dump into prompt context",
        "treating reference as universal truth",
    ],
    "governance_guardrail": "reference guidance never overrides policy, approval or security constraints",
}


STARTER_REFERENCE_PROFILES: tuple[ReferenceProfile, ...] = (
    ReferenceProfile(
        profile_id="ref.java.keycloak",
        language="java",
        framework="keycloak",
        project_type="backend_security_service",
        reference_source=ReferenceSource(
            repo="keycloak/keycloak",
            path_hint="services/, server-spi/, docs/",
            rationale="Large Java security-heavy backend with enterprise architecture patterns.",
        ),
        strengths=[
            "security-centric architecture",
            "enterprise backend modularity",
            "mature authn/authz patterns",
        ],
        limitations=[
            "not a universal Java template for all domains",
            "higher complexity than small-service baselines",
        ],
        intended_usage="Use for Java backend/security architecture guidance in new-project and evolution flows.",
        supported_flows=["new_project", "project_evolution"],
        provenance=ReferenceProvenance(
            owner="ananta",
            captured_at="2026-04-25T19:24:00+02:00",
            curation_note="Starter curated Java reference for security-heavy systems.",
        ),
    ),
    ReferenceProfile(
        profile_id="ref.python.ananta_backend",
        language="python",
        framework="flask",
        project_type="backend_orchestration_service",
        reference_source=ReferenceSource(
            repo="ananta888/ananta",
            path_hint="agent/, schemas/, policies/",
            rationale="Governance-aware orchestration backend with policy, approval, trace and artifact flows.",
        ),
        strengths=[
            "hub-worker orchestration boundaries",
            "policy and approval integration",
            "artifact-first execution surfaces",
        ],
        limitations=[
            "domain-specific architecture, not ideal for every lightweight Python project",
            "contains platform concerns that may be unnecessary for minimal APIs",
        ],
        intended_usage="Use for Python backend orchestration/governance patterns and service layering guidance.",
        supported_flows=["new_project", "project_evolution"],
        provenance=ReferenceProvenance(
            owner="ananta",
            captured_at="2026-04-25T19:24:00+02:00",
            curation_note="Starter curated Python reference from the native Ananta backend.",
        ),
    ),
    ReferenceProfile(
        profile_id="ref.angular.ananta_frontend",
        language="typescript",
        framework="angular",
        project_type="admin_workflow_frontend",
        reference_source=ReferenceSource(
            repo="ananta888/ananta",
            path_hint="frontend-angular/src/",
            rationale="Workflow-heavy Angular UI with operational surfaces and admin-oriented structure.",
        ),
        strengths=[
            "admin/workflow UI organization",
            "modular Angular application layout",
            "integration-friendly frontend conventions",
        ],
        limitations=[
            "not a universal Angular template for all consumer-facing apps",
            "workflow/admin focus may exceed needs of simple brochure apps",
        ],
        intended_usage="Use for Angular application structure and workflow-oriented UI guidance.",
        supported_flows=["new_project", "project_evolution"],
        provenance=ReferenceProvenance(
            owner="ananta",
            captured_at="2026-04-25T19:24:00+02:00",
            curation_note="Starter curated Angular reference from Ananta frontend surfaces.",
        ),
    ),
)


class ReferenceProfileService:
    """Curated starter reference profiles with bounded usage and deterministic selection."""

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.as_dict() for profile in STARTER_REFERENCE_PROFILES]

    def usage_boundary(self) -> dict[str, Any]:
        return dict(REFERENCE_USAGE_BOUNDARY)

    def selection_strategy(self) -> dict[str, Any]:
        return {
            "name": "deterministic_language_project_type_match_v1",
            "inputs": ["language", "project_type", "flow"],
            "rules": [
                "language exact match gets highest weight",
                "project_type exact/semantic match refines ranking",
                "flow support filters/reorders candidates",
                "stable tie-breaker by profile_id",
            ],
            "supports_flows": ["new_project", "project_evolution"],
        }

    def select_profile(
        self,
        *,
        language: str,
        project_type: str,
        flow: str,
    ) -> dict[str, Any] | None:
        normalized_language = str(language or "").strip().lower()
        normalized_project_type = str(project_type or "").strip().lower()
        normalized_flow = str(flow or "").strip().lower()
        ranked: list[tuple[float, ReferenceProfile]] = []
        for profile in STARTER_REFERENCE_PROFILES:
            score = 0.0
            if profile.language.lower() == normalized_language:
                score += 5.0
            if normalized_project_type and profile.project_type.lower() == normalized_project_type:
                score += 3.0
            if normalized_project_type and normalized_project_type in profile.project_type.lower():
                score += 1.0
            if normalized_flow and normalized_flow in {item.lower() for item in profile.supported_flows}:
                score += 1.0
            if score > 0:
                ranked.append((score, profile))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1].profile_id))
        winner = ranked[0][1]
        return {
            "profile": winner.as_dict(),
            "reason": {
                "language": winner.language,
                "project_type": winner.project_type,
                "flow": normalized_flow or "unspecified",
                "strategy": "deterministic_language_project_type_match_v1",
            },
        }

    def build_usage_audit_marker(
        self,
        *,
        profile_id: str,
        flow: str,
        task_or_goal_id: str,
    ) -> dict[str, Any]:
        selected = next((item for item in STARTER_REFERENCE_PROFILES if item.profile_id == profile_id), None)
        if selected is None:
            raise ValueError(f"unknown_reference_profile:{profile_id}")
        return {
            "reference_profile_id": selected.profile_id,
            "flow": str(flow or "").strip() or "unspecified",
            "task_or_goal_id": str(task_or_goal_id or "").strip(),
            "reference_source_repo": selected.reference_source.repo,
            "reference_source_path_hint": selected.reference_source.path_hint,
            "trace_visibility": "include_in_read_model_and_audit_when_reference_used",
        }

    def build_catalog_read_model(self) -> dict[str, Any]:
        items = []
        for profile in STARTER_REFERENCE_PROFILES:
            item = profile.as_dict()
            item["selection_hint"] = {
                "language": profile.language,
                "project_type": profile.project_type,
                "supported_flows": list(profile.supported_flows),
            }
            items.append(item)
        items.sort(key=lambda entry: (str(entry.get("language") or ""), str(entry.get("profile_id") or "")))
        return {
            "version": "v1",
            "usage_boundary": self.usage_boundary(),
            "selection_strategy": self.selection_strategy(),
            "items": items,
        }


reference_profile_service = ReferenceProfileService()


def get_reference_profile_service() -> ReferenceProfileService:
    return reference_profile_service
