from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from agent.db_models import PlanningPromptVersionDB
from agent.services.planning_prompt_evolution_guard_service import get_planning_prompt_evolution_guard_service
from agent.services.repository_registry import get_repository_registry

# Boundary note:
# This service may evolve planning prompt versions only (PlanningPromptVersionDB/profile hints).
# It must not mutate worker system prompts, role templates, overlays, governance, or tool contracts.


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

    def _should_evolve(self, *, run, policy: dict[str, Any], output_shape: str | None = None, parse_mode: str | None = None) -> tuple[bool, list[str]]:
        rules = dict(policy.get("planner_prompt_evolution") or {}) if isinstance(policy, dict) else {}
        if not bool(rules.get("enabled", True)):
            return False, ["disabled"]
        reasons: list[str] = []
        normalized_parse_mode = str(parse_mode or getattr(run, "parse_mode", "") or "").strip().lower()
        normalized_output_shape = str(output_shape or "").strip().lower()
        if normalized_parse_mode in {"parse_failed", "partial_json_objects", "bullet_fallback"}:
            reasons.append(f"parse_mode:{normalized_parse_mode}")
        if normalized_output_shape in {"partial_json", "markdown_bullets", "json_in_markdown_fence", "yaml_like"}:
            reasons.append(f"output_shape:{normalized_output_shape}")
        if str(run.parse_confidence or "").strip().lower() in {"low", ""}:
            reasons.append("low_parse_confidence")
        if int(run.repair_attempt_count or 0) >= int(rules.get("min_repair_attempts", 2)):
            reasons.append("high_repair_count")
        if not bool(run.validation_success):
            reasons.append("validation_failed")
        if str(run.error_classification or "").strip():
            reasons.append("error_classification")
        return bool(reasons), reasons

    def _mutate_template(self, template: str, *, reasons: list[str], output_format: str, output_shape: str | None = None, parse_mode: str | None = None, model_family: str | None = None) -> str:
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
        shape = str(output_shape or "").strip().lower()
        parse = str(parse_mode or "").strip().lower()
        family = str(model_family or "").strip().lower()
        if shape in {"json_in_markdown_fence", "partial_json"}:
            patch_rules.append("The model may naturally wrap JSON in markdown fences; accept and normalize that style.")
            patch_rules.append("If truncation happens, keep the same structure but shorten field values and descriptions.")
        if shape in {"markdown_bullets", "numbered_steps"}:
            patch_rules.append("The model may prefer markdown lists; keep the list style and map each item to a task object during normalization.")
        if shape == "yaml_like":
            patch_rules.append("The model may prefer YAML; keep keys short and unambiguous for later normalization.")
        if parse in {"parse_failed", "partial_json_objects", "bullet_fallback"}:
            patch_rules.append("Repair using the observed output style instead of forcing a new style.")
        if family:
            patch_rules.append(f"Model family hint: {family}. Keep prompts concise and family-aware.")

        patch_rules.append(f"Preferred output format: {output_format}.")
        block = "\n".join(f"- {r}" for r in patch_rules)
        if "Adaptive reinforcement rules:" in template:
            return template
        return f"{template}\n\nAdaptive reinforcement rules:\n{block}\n"

    def evolve_from_run(
        self,
        *,
        run,
        planning_policy: dict[str, Any] | None,
        activate_profile: bool = True,
        enabled: bool | None = None,
        output_shape: str | None = None,
        parse_mode: str | None = None,
        model_family: str | None = None,
    ) -> dict[str, Any]:
        policy = dict(planning_policy or {})
        rules = dict(policy.get("planner_prompt_evolution") or {}) if isinstance(policy, dict) else {}
        should, reasons = self._should_evolve(run=run, policy=policy, output_shape=output_shape, parse_mode=parse_mode)
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

        output_format = str((policy.get("preferred_output_format") or "json")).strip().lower() or "json"
        max_prompt_chars = int(rules.get("max_prompt_chars", 12000) or 12000)
        mutated_user = self._mutate_template(
            str(base.user_prompt_template or ""),
            reasons=reasons,
            output_format=output_format,
            output_shape=output_shape,
            parse_mode=parse_mode,
            model_family=model_family or self._infer_model_family(getattr(run, "model_name", None)),
        )
        mutated_repair = self._mutate_template(
            str(base.repair_prompt_template or ""),
            reasons=reasons,
            output_format=output_format,
            output_shape=output_shape,
            parse_mode=parse_mode,
            model_family=model_family or self._infer_model_family(getattr(run, "model_name", None)),
        )
        mutated_user = str(mutated_user or "")[:max_prompt_chars]
        mutated_repair = str(mutated_repair or "")[:max_prompt_chars]

        output_contract = dict(base.output_contract or {})
        output_contract.update(
            {
                "observed_parse_mode": str(parse_mode or getattr(run, "parse_mode", "") or ""),
                "observed_output_shape": str(output_shape or (getattr(run, "mode_data", {}) or {}).get("__output_shape__") or ""),
                "observed_model_family": model_family or self._infer_model_family(getattr(run, "model_name", None)) or base.target_model_family,
            }
        )
        system_rules = list(base.system_rules or [])
        if reasons:
            system_rules = [*system_rules, *[f"evolution_signal:{reason}" for reason in reasons if reason]]

        payload = {
            "version": f"{str(base.version)}.evo.{int(time.time())}",
            "language": str(base.language or "en"),
            "mode": str(base.mode or "generic"),
            "target_model_family": self._infer_model_family(getattr(run, "model_name", None)) or base.target_model_family,
            "output_contract": output_contract,
            "system_rules": system_rules,
            "user_prompt_template": mutated_user,
            "repair_prompt_template": mutated_repair,
            "enabled": bool((rules.get("auto_enable", False) if enabled is None else enabled)),
        }
        checksum = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()
        payload["checksum"] = checksum
        if str(base.checksum or "") == checksum:
            return {"evolved": False, "reason": "no_material_change"}
        ok, violations = get_planning_prompt_evolution_guard_service().validate_mutation(payload=payload)
        if not ok:
            return {"evolved": False, "reason": "evolver_scope_violation", "reason_codes": violations}

        # avoid duplicate by checksum
        for candidate in repos.planning_prompt_version_repo.get_enabled():
            if str(candidate.checksum or "") == checksum:
                evolved = candidate
                break
        else:
            evolved = repos.planning_prompt_version_repo.save(PlanningPromptVersionDB(**payload))

        profile_name = str(getattr(run, "planning_profile", "") or "").strip().lower()
        profile_updated = False
        if profile_name and bool(activate_profile) and bool(getattr(evolved, "enabled", False)):
            for p in repos.planning_model_profile_repo.get_enabled():
                if str(p.profile_name or "").strip().lower() == profile_name:
                    p.preferred_prompt_version_id = str(evolved.id)
                    repos.planning_model_profile_repo.save(p)
                    profile_updated = True
                    break

        return {
            "evolved": True,
            "new_prompt_version_id": str(evolved.id),
            "new_prompt_version": str(evolved.version),
            "reasons": reasons,
            "target_model_family": payload.get("target_model_family"),
            "profile_updated": profile_updated,
            "activated_profile": bool(activate_profile),
            "enabled": bool(getattr(evolved, "enabled", False)),
        }


_SERVICE = PlanningPromptEvolverService()


def get_planning_prompt_evolver_service() -> PlanningPromptEvolverService:
    return _SERVICE
