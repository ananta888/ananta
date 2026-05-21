from __future__ import annotations

from dataclasses import dataclass
from typing import Any


_GENERIC_TITLE_MARKERS = {
    "vorbereitung",
    "preparation",
    "setup environment",
    "environment setup",
    "initial setup",
    "allgemein",
    "misc",
}

_TEST_TEXT_MARKERS = {
    "test",
    "tests",
    "testing",
    "pytest",
    "unittest",
    "integration test",
    "smoke test",
    "regression test",
    "validation",
    "validate",
    "verify",
    "verification",
    "qa",
    "quality assurance",
    "assert",
    "coverage",
}

_REVIEW_TEXT_MARKERS = {
    "review",
    "documentation",
    "doc",
    "docs",
    "readme",
    "changelog",
    "handoff",
    "summary",
    "final check",
    "sign off",
    "sign-off",
    "release notes",
}

_DEFAULT_VALIDATION_PROFILES: dict[str, dict[str, Any]] = {
    "new_software_project": {
        "min_total_tasks": 5,
        "required_categories": {
            "analysis": 1,
            "infrastructure": 1,
            "implementation": 1,
            "tests": 1,
            "review": 1,
        },
        "max_generic_tasks": 2,
    },
    "generic": {
        "min_total_tasks": 3,
        "required_categories": {"implementation": 1},
        "max_generic_tasks": 1,
    },
}


@dataclass
class PlanningQualityResult:
    ok: bool
    reason: str
    missing_categories: list[str]
    generic_task_indices: list[int]
    details: dict[str, Any]


class PlanningQualityService:
    def _classify_task_category(self, task: dict[str, Any]) -> str:
        explicit = str(task.get("task_kind") or "").strip().lower()
        if explicit in {"analysis", "research", "planning"}:
            return "analysis"
        if explicit in {"ops"}:
            return "infrastructure"
        if explicit in {"coding"}:
            return "implementation"
        if explicit in {"testing"}:
            return "tests"
        if explicit in {"review", "doc"}:
            return "review"

        text = f"{task.get('title') or ''} {task.get('description') or ''}".lower()
        padded_text = f" {text} "
        if any(k in text for k in ["analy", "analyse", "plan", "requirement"]):
            return "analysis"
        if any(k in text for k in ["docker", "env", "infra", "pipeline", "install"]):
            return "infrastructure"
        if any(k in padded_text for k in [" github actions ", " continuous integration ", " ci/cd ", " ci-cd "]):
            return "infrastructure"
        if any(k in text for k in _TEST_TEXT_MARKERS):
            return "tests"
        if any(
            k in text
            for k in [
                "implement",
                "implementation",
                "implementierung",
                "umsetzen",
                "entwickeln",
                "programmieren",
                "code",
                "build",
                "create",
                "api",
                "endpoint",
            ]
        ):
            return "implementation"
        if any(k in text for k in _REVIEW_TEXT_MARKERS):
            return "review"
        return "review"

    def _is_generic_task(self, task: dict[str, Any]) -> bool:
        title = str(task.get("title") or "").strip().lower()
        desc = str(task.get("description") or "").strip().lower()
        if not title or len(desc) < 20:
            return True
        if title in _GENERIC_TITLE_MARKERS:
            return True
        # No concrete output hint and no executable intent
        concrete = any(k in desc for k in ["file", "command", "endpoint", "test", "artifact", "run", "implement", "code"])
        return not concrete

    def evaluate(
        self,
        *,
        subtasks: list[dict[str, Any]],
        mode: str,
        planning_policy: dict[str, Any] | None,
        team_id: str | None = None,
    ) -> PlanningQualityResult:
        policy = dict(planning_policy or {})
        validation_profiles = policy.get("validation_profiles") if isinstance(policy.get("validation_profiles"), dict) else {}
        if not validation_profiles:
            validation_profiles = dict(_DEFAULT_VALIDATION_PROFILES)
        profile = validation_profiles.get(str(mode or "generic")) if isinstance(validation_profiles.get(str(mode or "generic")), dict) else {}
        if not profile and str(mode or "").strip().lower() == "new_software_project":
            profile = dict(_DEFAULT_VALIDATION_PROFILES.get("new_software_project") or {})
        if not profile:
            profile = dict(_DEFAULT_VALIDATION_PROFILES.get("generic") or {})

        team_overrides = policy.get("team_overrides") if isinstance(policy.get("team_overrides"), dict) else {}
        if team_id and isinstance(team_overrides.get(str(team_id)), dict):
            # additive override only
            profile = {**profile, **dict(team_overrides.get(str(team_id)) or {})}

        min_total = max(1, int(profile.get("min_total_tasks") or 1))
        req_categories = profile.get("required_categories") if isinstance(profile.get("required_categories"), dict) else {}
        max_generic = max(0, int(profile.get("max_generic_tasks") or 0))

        counts: dict[str, int] = {}
        generic_idxs: list[int] = []
        for idx, task in enumerate(list(subtasks or []), start=1):
            cat = self._classify_task_category(task)
            counts[cat] = counts.get(cat, 0) + 1
            if self._is_generic_task(task):
                generic_idxs.append(idx)

        missing: list[str] = []
        for cat, req_min in req_categories.items():
            needed = max(0, int(req_min or 0))
            have = int(counts.get(str(cat), 0))
            if have < needed:
                missing.append(f"{cat}:{have}/{needed}")

        reasons: list[str] = []
        if len(list(subtasks or [])) < min_total:
            reasons.append(f"too_few_tasks:{len(list(subtasks or []))}/{min_total}")
        if missing:
            reasons.append("missing_categories:" + ",".join(missing))
        if len(generic_idxs) > max_generic:
            reasons.append(f"too_many_generic_tasks:{len(generic_idxs)}/{max_generic}")

        ok = not reasons
        return PlanningQualityResult(
            ok=ok,
            reason="ok" if ok else "|".join(reasons),
            missing_categories=missing,
            generic_task_indices=generic_idxs,
            details={"counts": counts, "min_total": min_total, "required_categories": req_categories, "max_generic_tasks": max_generic},
        )


_SERVICE = PlanningQualityService()


def get_planning_quality_service() -> PlanningQualityService:
    return _SERVICE
