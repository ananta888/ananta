from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from agent.db_models import PlanningPromptVersionDB
from agent.services.repository_registry import get_repository_registry


class PlanningPromptEvolverService:
    def _infer_model_family(self, model_name: str | None) -> str | None:
        normalized = str(model_name or "").strip().lower()
        if "gemma" in normalized:
            return "gemma"
        if "qwen" in normalized:
            return "qwen"
        if "llama" in normalized:
            return "llama"
        return None

    def _should_evolve(self, *, run, policy: dict[str, Any]) -> tuple[bool, list[str]]:
        rules = dict(policy.get("planner_prompt_evolution") or {}) if isinstance(policy, dict) else {}
        if not bool(rules.get("enabled", True)):
            return False, ["disabled"]
        reasons: list[str] = []
        if str(run.parse_confidence or "").strip().lower() in {"low", ""}:
            reasons.append("low_parse_confidence")
        if int(run.repair_attempt_count or 0) >= int(rules.get("min_repair_attempts", 2)):
            reasons.append("high_repair_count")
        if not bool(run.validation_success):
            reasons.append("validation_failed")
        if str(run.error_classification or "").strip():
            reasons.append("error_classification")
        return bool(reasons), reasons

    def _mutate_template(self, template: str, *, reasons: list[str], output_format: str) -> str:
        patch_rules: list[str] = []
        if "low_parse_confidence" in reasons:
            patch_rules.append("Use compact fields and avoid extra prose.")
            patch_rules.append("Prefer one task per line/object with explicit title and concrete output.")
        if "high_repair_count" in reasons:
            patch_rules.append("Increase structural clarity: explicit dependencies and task_kind per task.")
        if "validation_failed" in reasons:
            patch_rules.append("Ensure category coverage expected by validator profile.")
        if "error_classification" in reasons:
            patch_rules.append("Avoid ambiguous or generic tasks; include concrete artifact/command in each actionable item.")

        patch_rules.append(f"Preferred output format: {output_format}.")
        block = "\n".join(f"- {r}" for r in patch_rules)
        if "Adaptive reinforcement rules:" in template:
            return template
        return f"{template}\n\nAdaptive reinforcement rules:\n{block}\n"

    def evolve_from_run(self, *, run, planning_policy: dict[str, Any] | None) -> dict[str, Any]:
        policy = dict(planning_policy or {})
        should, reasons = self._should_evolve(run=run, policy=policy)
        if not should:
            return {"evolved": False, "reason": "no_trigger"}

        repos = get_repository_registry()
        base = repos.planning_prompt_version_repo.get_by_id(str(run.prompt_version_id or "")) if str(run.prompt_version_id or "") else None
        if base is None:
            # Fallback: choose latest enabled prompt by mode + model family
            mode = str(getattr(run, "mode", "") or "generic").strip() or "generic"
            model_family = self._infer_model_family(getattr(run, "model_name", None))
            enabled = repos.planning_prompt_version_repo.get_enabled()
            for candidate in enabled:
                if str(candidate.mode or "").strip() != mode:
                    continue
                target = str(candidate.target_model_family or "").strip().lower()
                if model_family and target and target != model_family:
                    continue
                base = candidate
                break
        if base is None:
            return {"evolved": False, "reason": "base_prompt_missing"}

        output_format = str(
            (policy.get("preferred_output_format") or "json")
        ).strip().lower() or "json"
        mutated_user = self._mutate_template(str(base.user_prompt_template or ""), reasons=reasons, output_format=output_format)
        mutated_repair = self._mutate_template(str(base.repair_prompt_template or ""), reasons=reasons, output_format=output_format)

        payload = {
            "version": f"{str(base.version)}.evo.{int(time.time())}",
            "language": str(base.language or "en"),
            "mode": str(base.mode or "generic"),
            "target_model_family": self._infer_model_family(getattr(run, "model_name", None)) or base.target_model_family,
            "output_contract": dict(base.output_contract or {}),
            "system_rules": list(base.system_rules or []),
            "user_prompt_template": mutated_user,
            "repair_prompt_template": mutated_repair,
            "enabled": True,
        }
        checksum = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        payload["checksum"] = checksum

        # avoid duplicate by checksum
        for candidate in repos.planning_prompt_version_repo.get_enabled():
            if str(candidate.checksum or "") == checksum:
                evolved = candidate
                break
        else:
            evolved = repos.planning_prompt_version_repo.save(PlanningPromptVersionDB(**payload))

        profile_name = str(getattr(run, "planning_profile", "") or "").strip().lower()
        if profile_name:
            for p in repos.planning_model_profile_repo.get_enabled():
                if str(p.profile_name or "").strip().lower() == profile_name:
                    p.preferred_prompt_version_id = str(evolved.version)
                    repos.planning_model_profile_repo.save(p)
                    break

        return {
            "evolved": True,
            "new_prompt_version_id": str(evolved.id),
            "new_prompt_version": str(evolved.version),
            "reasons": reasons,
            "target_model_family": payload.get("target_model_family"),
        }


_SERVICE = PlanningPromptEvolverService()


def get_planning_prompt_evolver_service() -> PlanningPromptEvolverService:
    return _SERVICE
