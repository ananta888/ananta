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


REFERENCE_PATTERN_CATEGORIES: tuple[dict[str, Any], ...] = (
    {
        "id": "project_structure",
        "title": "Project structure",
        "description": "Repository and package/module layout for scalable delivery.",
        "retrieval_tags": ["structure", "layout", "modules"],
    },
    {
        "id": "module_layering",
        "title": "Module layering",
        "description": "Boundaries between domain, service, API, persistence and infrastructure layers.",
        "retrieval_tags": ["layering", "boundaries", "architecture"],
    },
    {
        "id": "configuration_style",
        "title": "Configuration style",
        "description": "Runtime configuration, environment separation and safe defaults.",
        "retrieval_tags": ["config", "runtime", "defaults"],
    },
    {
        "id": "test_patterns",
        "title": "Test patterns",
        "description": "Unit/integration/end-to-end patterns and quality gate shapes.",
        "retrieval_tags": ["tests", "quality", "gates"],
    },
    {
        "id": "api_layout",
        "title": "API layout",
        "description": "Route and contract organization for readable and evolvable APIs.",
        "retrieval_tags": ["api", "contracts", "routing"],
    },
    {
        "id": "security_conventions",
        "title": "Security conventions",
        "description": "Approval, policy and audit conventions for governed change flows.",
        "retrieval_tags": ["security", "policy", "approval", "audit"],
    },
)


REFERENCE_RETRIEVAL_ENTRY_POINTS: dict[str, Any] = {
    "mode": "bounded_reference_retrieval_v1",
    "global_bounds": {
        "allowed_source_kinds": ["curated_reference_profile", "reference_chunk", "reference_pattern_note"],
        "forbidden_source_kinds": ["full_repository_dump", "raw_unbounded_history"],
        "max_chunks_per_profile": 24,
        "max_total_chunks": 48,
    },
    "flows": {
        "new_project": {
            "entry_points": [
                "reference_profile_catalog",
                "reference_pattern_categories",
                "project_skeleton_guidance",
            ],
            "focus_categories": [
                "project_structure",
                "module_layering",
                "configuration_style",
                "test_patterns",
            ],
        },
        "project_evolution": {
            "entry_points": [
                "reference_profile_catalog",
                "reference_pattern_categories",
                "reference_change_recommendation",
            ],
            "focus_categories": [
                "module_layering",
                "test_patterns",
                "api_layout",
                "security_conventions",
            ],
        },
    },
}


REFERENCE_CHUNKING_INDEXING_STRATEGY: dict[str, Any] = {
    "schema": "reference_chunking_indexing_v1",
    "chunking": {
        "unit": "architecture_focused_text_slice",
        "target_chars": 1400,
        "max_chars": 2200,
        "overlap_chars": 160,
    },
    "indexing": {
        "required_metadata": [
            "reference_profile_id",
            "pattern_category",
            "source_repo",
            "source_path_hint",
            "provenance_owner",
        ],
        "retention": "profile_scoped_and_traceable",
    },
    "guardrails": {
        "provenance_required": True,
        "reject_unbounded_repository_context": True,
    },
}


REFERENCE_INTEGRATION_HINTS: dict[str, dict[str, str]] = {
    "ref.java.keycloak": {
        "blueprint_name": "secure-java-backend-starter",
        "work_profile": "security_backend",
        "retrieval_intent": "reference_java_security_architecture",
    },
    "ref.python.ananta_backend": {
        "blueprint_name": "ananta-python-backend-starter",
        "work_profile": "governed_backend_orchestration",
        "retrieval_intent": "reference_python_orchestration_architecture",
    },
    "ref.angular.ananta_frontend": {
        "blueprint_name": "ananta-angular-workflow-starter",
        "work_profile": "workflow_frontend",
        "retrieval_intent": "reference_angular_workflow_architecture",
    },
}


REFERENCE_SKELETON_GUIDANCE: dict[str, list[str]] = {
    "ref.java.keycloak": [
        "Start with clear domain/security module boundaries and explicit API contracts.",
        "Separate authentication/authorization concerns from business feature modules.",
        "Add integration tests for identity and policy-sensitive flows from day one.",
    ],
    "ref.python.ananta_backend": [
        "Separate orchestration, policy/governance, persistence and transport surfaces.",
        "Use explicit service boundaries and repository access via clear interfaces.",
        "Include traceability metadata in execution paths and result artifacts.",
    ],
    "ref.angular.ananta_frontend": [
        "Organize UI by workflow domains with dedicated service/data boundaries.",
        "Keep API adapters and presentation state separated for maintainable growth.",
        "Model review/approval states explicitly in UI flows instead of hidden toggles.",
    ],
}


class ReferenceProfileService:
    """Curated starter reference profiles with bounded usage and deterministic selection."""

    def list_profiles(self) -> list[dict[str, Any]]:
        return [profile.as_dict() for profile in STARTER_REFERENCE_PROFILES]

    def usage_boundary(self) -> dict[str, Any]:
        return dict(REFERENCE_USAGE_BOUNDARY)

    def pattern_categories(self) -> list[dict[str, Any]]:
        return [dict(category) for category in REFERENCE_PATTERN_CATEGORIES]

    def retrieval_entry_points(self) -> dict[str, Any]:
        return {
            "mode": REFERENCE_RETRIEVAL_ENTRY_POINTS["mode"],
            "global_bounds": dict(REFERENCE_RETRIEVAL_ENTRY_POINTS["global_bounds"]),
            "flows": {
                key: {
                    "entry_points": list((value or {}).get("entry_points") or []),
                    "focus_categories": list((value or {}).get("focus_categories") or []),
                }
                for key, value in dict(REFERENCE_RETRIEVAL_ENTRY_POINTS.get("flows") or {}).items()
            },
        }

    def chunking_indexing_strategy(self) -> dict[str, Any]:
        return {
            "schema": REFERENCE_CHUNKING_INDEXING_STRATEGY["schema"],
            "chunking": dict(REFERENCE_CHUNKING_INDEXING_STRATEGY["chunking"]),
            "indexing": dict(REFERENCE_CHUNKING_INDEXING_STRATEGY["indexing"]),
            "guardrails": dict(REFERENCE_CHUNKING_INDEXING_STRATEGY["guardrails"]),
        }

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
        ranked: list[tuple[float, ReferenceProfile, list[str]]] = []
        for profile in STARTER_REFERENCE_PROFILES:
            score = 0.0
            matched_signals: list[str] = []
            if profile.language.lower() == normalized_language:
                score += 5.0
                matched_signals.append("language_exact")
            if normalized_project_type and profile.project_type.lower() == normalized_project_type:
                score += 3.0
                matched_signals.append("project_type_exact")
            if normalized_project_type and normalized_project_type in profile.project_type.lower():
                score += 1.0
                matched_signals.append("project_type_semantic")
            if normalized_flow and normalized_flow in {item.lower() for item in profile.supported_flows}:
                score += 1.0
                matched_signals.append("flow_supported")
            if score > 0:
                ranked.append((score, profile, matched_signals))
        if not ranked:
            return None
        ranked.sort(key=lambda item: (-item[0], item[1].profile_id))
        winning_score, winner, matched_signals = ranked[0]
        return {
            "profile": winner.as_dict(),
            "reason": {
                "language": winner.language,
                "project_type": winner.project_type,
                "flow": normalized_flow or "unspecified",
                "strategy": "deterministic_language_project_type_match_v1",
                "score": winning_score,
                "matched_signals": matched_signals,
                "summary": self._build_reason_summary(winner.profile_id, matched_signals),
            },
        }

    def recommend_for_flow(self, *, flow: str, mode_data: dict[str, Any]) -> dict[str, Any]:
        normalized_flow = str(flow or "").strip().lower() or "new_project"
        payload = dict(mode_data or {})
        preferred_profile_id = str(payload.get("reference_profile_id") or "").strip()
        if preferred_profile_id:
            preferred_profile = next(
                (profile for profile in STARTER_REFERENCE_PROFILES if profile.profile_id == preferred_profile_id),
                None,
            )
            if preferred_profile is not None:
                manual_reason = {
                    "language": preferred_profile.language,
                    "project_type": preferred_profile.project_type,
                    "flow": normalized_flow,
                    "strategy": "manual_profile_selection",
                    "score": 100.0,
                    "matched_signals": ["manual_profile_id"],
                    "summary": self._build_reason_summary(preferred_profile.profile_id, ["manual_profile_id"]),
                }
                return {
                    "flow": normalized_flow,
                    "selection_basis": {
                        "preferred_profile_id": preferred_profile_id,
                        "language_candidates": [],
                        "project_type_candidates": [],
                    },
                    "recommendations": [{"profile": preferred_profile.as_dict(), "reason": manual_reason}],
                    "selected_profile": preferred_profile.as_dict(),
                    "selected_reason": manual_reason,
                }

        language_candidates = self._infer_language_candidates(payload)
        project_type_candidates = self._infer_project_type_candidates(payload, flow=normalized_flow)
        recommendations: list[dict[str, Any]] = []
        for candidate_language in language_candidates:
            for candidate_project_type in project_type_candidates:
                selected = self.select_profile(
                    language=candidate_language,
                    project_type=candidate_project_type,
                    flow=normalized_flow,
                )
                if selected is None:
                    continue
                recommendations.append(selected)
        deduped: list[dict[str, Any]] = []
        seen = set()
        for item in recommendations:
            profile = dict(item.get("profile") or {})
            reason = dict(item.get("reason") or {})
            profile_id = str(profile.get("profile_id") or "")
            if not profile_id or profile_id in seen:
                continue
            seen.add(profile_id)
            deduped.append({"profile": profile, "reason": reason})
        deduped.sort(key=lambda item: (-float((item.get("reason") or {}).get("score") or 0.0), item["profile"]["profile_id"]))
        selected_profile = dict((deduped[0] if deduped else {}).get("profile") or {})
        selected_reason = dict((deduped[0] if deduped else {}).get("reason") or {})
        return {
            "flow": normalized_flow,
            "selection_basis": {
                "preferred_profile_id": None,
                "language_candidates": language_candidates,
                "project_type_candidates": project_type_candidates,
            },
            "recommendations": deduped,
            "selected_profile": selected_profile or None,
            "selected_reason": selected_reason or None,
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

    def integration_hints_for_profile(self, profile_id: str) -> dict[str, Any]:
        hints = dict(REFERENCE_INTEGRATION_HINTS.get(str(profile_id or "").strip()) or {})
        if not hints:
            return {
                "reference_profile_id": str(profile_id or "").strip(),
                "blueprint_name": "generic-reference-starter",
                "work_profile": "general",
                "retrieval_intent": "reference_guided_delivery",
            }
        return {"reference_profile_id": str(profile_id or "").strip(), **hints}

    def build_project_skeleton_guidance(self, *, profile_id: str, flow: str) -> dict[str, Any]:
        normalized_profile_id = str(profile_id or "").strip()
        guidance_lines = list(REFERENCE_SKELETON_GUIDANCE.get(normalized_profile_id) or [])
        if not guidance_lines:
            guidance_lines = [
                "Start with explicit module boundaries and small reviewable increments.",
                "Keep policy, verification and test surfaces visible from the first iteration.",
            ]
        return {
            "profile_id": normalized_profile_id,
            "flow": str(flow or "").strip() or "unspecified",
            "guidance_lines": guidance_lines,
            "boundary_note": "Use reference guidance for architecture patterns, never as blind-copy template.",
        }

    def build_retrieval_contract(self) -> dict[str, Any]:
        return {
            "version": "v1",
            "entry_points": self.retrieval_entry_points(),
            "pattern_categories": self.pattern_categories(),
            "chunking_indexing_strategy": self.chunking_indexing_strategy(),
            "usage_boundary": self.usage_boundary(),
        }

    def build_mode_reference_plan(self, *, flow: str, mode_data: dict[str, Any]) -> dict[str, Any]:
        recommendation = self.recommend_for_flow(flow=flow, mode_data=mode_data)
        selected_profile = dict(recommendation.get("selected_profile") or {})
        selected_profile_id = str(selected_profile.get("profile_id") or "").strip()
        integration_hints = self.integration_hints_for_profile(selected_profile_id) if selected_profile_id else {}
        skeleton_guidance = (
            self.build_project_skeleton_guidance(profile_id=selected_profile_id, flow=flow)
            if selected_profile_id
            else None
        )
        return {
            "selection": recommendation,
            "retrieval": {
                "entry_points": self.retrieval_entry_points(),
                "pattern_categories": self.pattern_categories(),
                "chunking_indexing_strategy": self.chunking_indexing_strategy(),
            },
            "integration_hints": integration_hints,
            "skeleton_guidance": skeleton_guidance,
            "usage_boundary": self.usage_boundary(),
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
            "pattern_categories": self.pattern_categories(),
            "retrieval_entry_points": self.retrieval_entry_points(),
            "chunking_indexing_strategy": self.chunking_indexing_strategy(),
            "items": items,
        }

    def _infer_language_candidates(self, mode_data: dict[str, Any]) -> list[str]:
        preferred_stack = str(mode_data.get("preferred_stack") or "").strip().lower()
        platform = str(mode_data.get("platform") or "").strip().lower()
        combined = f"{preferred_stack} {platform}"
        candidates: list[str] = []
        if "java" in combined:
            candidates.append("java")
        if "python" in combined:
            candidates.append("python")
        if "angular" in combined or "typescript" in combined:
            candidates.append("typescript")
        if "frontend" in combined:
            candidates.append("typescript")
        if "backend" in combined or "api" in combined:
            candidates.append("python")
        if "security" in combined or "auth" in combined:
            candidates.append("java")
        if not candidates:
            return ["python", "typescript", "java"]
        deduped = []
        for item in candidates:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _infer_project_type_candidates(self, mode_data: dict[str, Any], *, flow: str) -> list[str]:
        preferred_stack = str(mode_data.get("preferred_stack") or "").strip().lower()
        project_idea = str(mode_data.get("project_idea") or mode_data.get("change_goal") or "").strip().lower()
        platform = str(mode_data.get("platform") or "").strip().lower()
        combined = f"{preferred_stack} {project_idea} {platform} {flow}".strip()
        candidates: list[str] = []
        if "security" in combined or "auth" in combined or "keycloak" in combined:
            candidates.append("backend_security_service")
        if "backend" in combined or "api" in combined or "orchestration" in combined:
            candidates.append("backend_orchestration_service")
        if "frontend" in combined or "workflow" in combined or "ui" in combined or "web" in combined:
            candidates.append("admin_workflow_frontend")
        if not candidates:
            return [
                "backend_orchestration_service",
                "admin_workflow_frontend",
                "backend_security_service",
            ]
        deduped = []
        for item in candidates:
            if item not in deduped:
                deduped.append(item)
        return deduped

    def _build_reason_summary(self, profile_id: str, matched_signals: list[str]) -> str:
        signal_text = ", ".join(matched_signals) if matched_signals else "baseline profile fit"
        return f"{profile_id} selected via {signal_text}"


reference_profile_service = ReferenceProfileService()


def get_reference_profile_service() -> ReferenceProfileService:
    return reference_profile_service
