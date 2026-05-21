from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent.db_models import PlanningPromptVersionDB
from agent.services.repository_registry import get_repository_registry


@dataclass(frozen=True)
class ResolvedPlanningPrompt:
    prompt_version_id: str
    version: str
    language: str
    mode: str
    prompt: str
    checksum: str
    source_path: str = "config/planning_prompts.default.json"
    is_inline_fallback: bool = False
    optimizer_steps: tuple = ()  # tuple of dicts for hashability


_DEFAULT_PATH = Path("config/planning_prompts.default.json")


class PlanningPromptRegistry:
    def _checksum(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _load_defaults(self) -> list[dict[str, Any]]:
        if not _DEFAULT_PATH.exists():
            return []
        raw = json.loads(_DEFAULT_PATH.read_text(encoding="utf-8"))
        return [dict(item) for item in list(raw.get("prompt_versions") or []) if isinstance(item, dict)]

    def ensure_default_versions(self) -> None:
        repo = get_repository_registry().planning_prompt_version_repo
        existing = repo.get_enabled()
        existing_versions = {str(item.version or "").strip() for item in existing}
        for item in self._load_defaults():
            payload = {
                "version": str(item.get("version") or "v1"),
                "language": str(item.get("language") or "de"),
                "mode": str(item.get("mode") or "generic"),
                "target_model_family": item.get("target_model_family"),
                "output_contract": dict(item.get("output_contract") or {}),
                "system_rules": list(item.get("system_rules") or []),
                "user_prompt_template": str(item.get("user_prompt_template") or ""),
                "repair_prompt_template": str(item.get("repair_prompt_template") or ""),
                "enabled": bool(item.get("enabled", True)),
            }
            if payload["version"] in existing_versions:
                continue
            checksum = self._checksum(payload)
            repo.save(PlanningPromptVersionDB(**payload, checksum=checksum))

    def resolve(
        self,
        *,
        goal: str,
        context: str | None,
        mode: str,
        language: str,
        model_family: str | None = None,
        preferred_prompt_version_id: str | None = None,
        preferred_output_format: str | None = None,
        domain_hints: list[str] | None = None,
        behavior_profile: dict[str, Any] | None = None,
    ) -> ResolvedPlanningPrompt:
        self.ensure_default_versions()
        candidates = get_repository_registry().planning_prompt_version_repo.get_enabled()
        lang = str(language or "de").strip().lower() or "de"
        selected = None
        preferred_id = str(preferred_prompt_version_id or "").strip()
        if preferred_id:
            for item in candidates:
                if (
                    str(item.id or "").strip() == preferred_id
                    or str(item.version or "").strip() == preferred_id
                ) and bool(item.enabled):
                    selected = item
                    break
        for item in candidates:
            if selected is not None:
                break
            if str(item.mode or "").strip() != str(mode or "generic"):
                continue
            if str(item.language or "").strip().lower() != lang:
                continue
            target_family = str(item.target_model_family or "").strip().lower()
            if target_family and target_family != str(model_family or "").strip().lower():
                continue
            selected = item
            break
        if selected is None:
            # fallback generic+lang, then generic+de
            for item in candidates:
                if str(item.mode or "").strip() == str(mode or "generic") and str(item.language or "").strip().lower() == lang:
                    selected = item
                    break
            if selected is None:
                for item in candidates:
                    if str(item.mode or "").strip() == "generic" and str(item.language or "").strip().lower() == "de":
                        selected = item
                        break
        if selected is None:
            # absolute fallback to in-memory template
            template = (
                "You are a planning assistant. Produce a valid JSON array of concrete tasks.\n\n"
                "ZIEL:\n{goal}\n\n"
                "CONTEXT:\n{context}\n\n"
                "Rules:\n"
                "- Return only JSON, no markdown fences.\n"
                "- Prefer 5 to 8 tasks for software goals.\n"
                "- Include setup, implementation, execution, verification, and summary when relevant.\n"
                "- Each task needs title, description, priority, depends_on.\n"
                "- Preferred output format: {preferred_output_format}.\n"
            )
            rendered = template.format(
                goal=goal,
                context=context or "",
                preferred_output_format=str(preferred_output_format or "json").strip().lower() or "json",
            )
            return ResolvedPlanningPrompt(
                prompt_version_id="inline-fallback",
                version="inline-fallback",
                language=lang,
                mode=mode,
                prompt=rendered,
                checksum=self._checksum({"template": template, "lang": lang, "mode": mode}),
                is_inline_fallback=True,
            )

        hints_block = ""
        for idx, hint in enumerate(list(domain_hints or []), start=1):
            text = str(hint or "").strip()
            if text:
                hints_block += f"{idx}. {text}\n"
        hints_block = hints_block.strip()
        rendered = str(selected.user_prompt_template or "").format(
            goal=goal,
            context=context or "",
            preferred_output_format=(str(preferred_output_format or "").strip().lower() or "json"),
            domain_hints_block=hints_block,
        )
        opt_steps: list[dict] = []
        try:
            from agent.services.planning_prompt_optimizer_service import get_planning_prompt_optimizer_service
            import hashlib as _hl

            _input_hash = _hl.sha256(rendered.encode("utf-8", errors="replace")).hexdigest()
            rendered, _style = get_planning_prompt_optimizer_service().optimize(
                prompt=rendered,
                preferred_output_format=preferred_output_format,
                behavior_profile=behavior_profile,
            )
            _output_hash = _hl.sha256(rendered.encode("utf-8", errors="replace")).hexdigest()
            opt_steps.append({
                "name": "planning_prompt_optimizer",
                "style": _style,
                "changed": _input_hash != _output_hash,
                "input_hash": _input_hash,
                "output_hash": _output_hash,
            })
        except Exception:
            pass
        return ResolvedPlanningPrompt(
            prompt_version_id=str(selected.id),
            version=str(selected.version),
            language=str(selected.language),
            mode=str(selected.mode),
            prompt=rendered,
            checksum=str(selected.checksum),
            is_inline_fallback=False,
            optimizer_steps=tuple(opt_steps),
        )


_SERVICE = PlanningPromptRegistry()


def get_planning_prompt_registry() -> PlanningPromptRegistry:
    return _SERVICE
