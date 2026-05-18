from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any

from agent.db_models import PlanningModelProfileDB
from agent.services.repository_registry import get_repository_registry


_DEFAULT_PATH = Path("config/planning_model_profiles.default.json")


def _normalize_notes(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    try:
        return json.dumps(value, ensure_ascii=True, separators=(",", ":"))
    except Exception:
        text = str(value).strip()
        return text or None


def _extract_preferred_output_format(notes: Any) -> str:
    allowed = {"json", "markdown", "yaml"}
    if isinstance(notes, str):
        text = notes.strip()
        if not text:
            return "json"
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                fmt = str(parsed.get("preferred_output_format") or "").strip().lower()
                if fmt in allowed:
                    return fmt
        except Exception:
            pass
        if text.lower().startswith("preferred_output_format="):
            fmt = text.split("=", 1)[1].strip().lower()
            if fmt in allowed:
                return fmt
    elif isinstance(notes, dict):
        fmt = str(notes.get("preferred_output_format") or "").strip().lower()
        if fmt in allowed:
            return fmt
    return "json"


class PlanningModelProfileService:
    def _load_defaults(self) -> list[dict[str, Any]]:
        if not _DEFAULT_PATH.exists():
            return []
        raw = json.loads(_DEFAULT_PATH.read_text(encoding="utf-8"))
        return [dict(item) for item in list(raw.get("profiles") or []) if isinstance(item, dict)]

    def ensure_default_profiles(self) -> None:
        repo = get_repository_registry().planning_model_profile_repo
        existing = repo.get_enabled()
        existing_by_key = {
            (
                str(item.provider or "").strip().lower(),
                str(item.model_name_pattern or "").strip().lower(),
                str(item.profile_name or "").strip().lower(),
            ): item
            for item in existing
        }
        for item in self._load_defaults():
            key = (
                str(item.get("provider") or "").strip().lower(),
                str(item.get("model_name_pattern") or "").strip().lower(),
                str(item.get("profile_name") or "default").strip().lower(),
            )
            existing_profile = existing_by_key.get(key)
            if existing_profile is not None:
                existing_profile.model_family = item.get("model_family")
                existing_profile.prompt_language = str(item.get("prompt_language") or "de")
                existing_profile.context_max_chars = int(item.get("context_max_chars") or 1200)
                existing_profile.max_output_tokens = int(item.get("max_output_tokens") or 1024)
                existing_profile.temperature = float(item.get("temperature") or 0.2)
                existing_profile.repair_attempts = int(item.get("repair_attempts") or 2)
                existing_profile.repair_strategies = list(item.get("repair_strategies") or [])
                existing_profile.preferred_prompt_version_id = item.get("preferred_prompt_version_id")
                existing_profile.output_contract_strictness = str(item.get("output_contract_strictness") or "repair_required")
                existing_profile.supports_json_mode = bool(item.get("supports_json_mode", False))
                existing_profile.requires_english_prompt = bool(item.get("requires_english_prompt", False))
                existing_profile.notes = _normalize_notes(item.get("notes"))
                existing_profile.enabled = bool(item.get("enabled", True))
                repo.save(existing_profile)
                continue
            repo.save(PlanningModelProfileDB(
                    provider=str(item.get("provider") or ""),
                    model_name_pattern=item.get("model_name_pattern"),
                    model_family=item.get("model_family"),
                    profile_name=str(item.get("profile_name") or "default"),
                    prompt_language=str(item.get("prompt_language") or "de"),
                    context_max_chars=int(item.get("context_max_chars") or 1200),
                    max_output_tokens=int(item.get("max_output_tokens") or 1024),
                    temperature=float(item.get("temperature") or 0.2),
                    repair_attempts=int(item.get("repair_attempts") or 2),
                    repair_strategies=list(item.get("repair_strategies") or []),
                    preferred_prompt_version_id=item.get("preferred_prompt_version_id"),
                    output_contract_strictness=str(item.get("output_contract_strictness") or "repair_required"),
                    supports_json_mode=bool(item.get("supports_json_mode", False)),
                    requires_english_prompt=bool(item.get("requires_english_prompt", False)),
                    notes=_normalize_notes(item.get("notes")),
                    enabled=bool(item.get("enabled", True)),
                ))

    def resolve_profile(self, *, provider: str | None, model_name: str | None, explicit_profile: str | None = None) -> dict[str, Any]:
        self.ensure_default_profiles()
        provider_norm = str(provider or "").strip().lower()
        model_norm = str(model_name or "").strip().lower()
        profiles = get_repository_registry().planning_model_profile_repo.get_enabled()

        if explicit_profile:
            for p in profiles:
                if str(p.profile_name or "").strip().lower() == str(explicit_profile).strip().lower():
                    return self._to_dict(p)

        # exact / pattern
        best = None
        for p in profiles:
            if str(p.provider or "").strip().lower() not in {provider_norm, "*", "default", ""}:
                continue
            pattern = str(p.model_name_pattern or "").strip().lower()
            if pattern and fnmatch.fnmatch(model_norm, pattern):
                best = p
                break
            if pattern and pattern == model_norm:
                best = p
                break
        if best is not None:
            return self._to_dict(best)

        # provider default
        for p in profiles:
            if str(p.provider or "").strip().lower() == provider_norm and not str(p.model_name_pattern or "").strip():
                return self._to_dict(p)

        # global default
        for p in profiles:
            if str(p.profile_name or "").strip().lower() == "global_default":
                return self._to_dict(p)
        return {
            "profile_name": "global_default",
            "prompt_language": "de",
            "context_max_chars": 1200,
            "max_output_tokens": 1024,
            "temperature": 0.2,
            "repair_attempts": 2,
            "repair_strategies": [],
            "output_contract_strictness": "repair_required",
            "supports_json_mode": False,
            "requires_english_prompt": False,
            "preferred_output_format": "json",
        }

    @staticmethod
    def _to_dict(profile: PlanningModelProfileDB) -> dict[str, Any]:
        notes = profile.notes
        return {
            "id": str(profile.id),
            "provider": str(profile.provider or ""),
            "model_name_pattern": profile.model_name_pattern,
            "model_family": profile.model_family,
            "profile_name": str(profile.profile_name or ""),
            "prompt_language": str(profile.prompt_language or "de"),
            "context_max_chars": int(profile.context_max_chars or 1200),
            "max_output_tokens": int(profile.max_output_tokens or 1024),
            "temperature": float(profile.temperature or 0.2),
            "repair_attempts": int(profile.repair_attempts or 2),
            "repair_strategies": list(profile.repair_strategies or []),
            "preferred_prompt_version_id": profile.preferred_prompt_version_id,
            "output_contract_strictness": str(profile.output_contract_strictness or "repair_required"),
            "supports_json_mode": bool(profile.supports_json_mode),
            "requires_english_prompt": bool(profile.requires_english_prompt),
            "preferred_output_format": _extract_preferred_output_format(notes),
            "notes": profile.notes,
        }


_SERVICE = PlanningModelProfileService()


def get_planning_model_profile_service() -> PlanningModelProfileService:
    return _SERVICE
