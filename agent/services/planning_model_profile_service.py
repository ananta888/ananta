from __future__ import annotations

import fnmatch
import json
from pathlib import Path
from typing import Any
import time

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


def _normalize_learning_state(value: Any, *, default_state: str = "stable") -> dict[str, Any]:
    state = str(default_state or "stable").strip().lower() or "stable"
    observed_output_format = None
    observed_model_family = None
    prompt_version_id = None
    sample_size = None
    reason_codes: list[str] = []
    source = "default"
    updated_at = time.time()

    if isinstance(value, str):
        candidate_state = value.strip().lower()
        if candidate_state:
            state = candidate_state
    elif isinstance(value, dict):
        source = str(value.get("source") or source).strip() or source
        candidate_state = str(value.get("state") or "").strip().lower()
        if candidate_state:
            state = candidate_state
        observed_output_format = str(value.get("observed_output_format") or "").strip() or None
        observed_model_family = str(value.get("observed_model_family") or "").strip() or None
        prompt_version_id = str(value.get("prompt_version_id") or "").strip() or None
        try:
            sample_size = int(value.get("sample_size")) if value.get("sample_size") is not None else None
        except (TypeError, ValueError):
            sample_size = None
        reason_codes = [str(item or "").strip() for item in list(value.get("reason_codes") or []) if str(item or "").strip()]
        try:
            updated_at = float(value.get("updated_at") or updated_at)
        except (TypeError, ValueError):
            updated_at = time.time()

    if state not in {"learning", "candidate", "stable", "degraded"}:
        state = default_state if default_state in {"learning", "candidate", "stable", "degraded"} else "stable"

    payload = {
        "state": state,
        "source": source,
        "updated_at": updated_at,
    }
    if observed_output_format:
        payload["observed_output_format"] = observed_output_format
    if observed_model_family:
        payload["observed_model_family"] = observed_model_family
    if prompt_version_id:
        payload["prompt_version_id"] = prompt_version_id
    if sample_size is not None:
        payload["sample_size"] = max(0, int(sample_size))
    if reason_codes:
        payload["reason_codes"] = reason_codes
    return payload


def normalize_learning_state(value: Any, *, default_state: str = "stable") -> dict[str, Any]:
    return _normalize_learning_state(value, default_state=default_state)


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
                existing_profile.repair_attempts = int(item["repair_attempts"] if item.get("repair_attempts") is not None else 2)
                existing_profile.repair_strategies = list(item.get("repair_strategies") or [])
                existing_profile.preferred_prompt_version_id = item.get("preferred_prompt_version_id")
                existing_profile.output_contract_strictness = str(item.get("output_contract_strictness") or "repair_required")
                existing_profile.supports_json_mode = bool(item.get("supports_json_mode", False))
                existing_profile.requires_english_prompt = bool(item.get("requires_english_prompt", False))
                current_learning_state = getattr(existing_profile, "learning_state", None)
                if isinstance(item.get("learning_state"), (dict, str)):
                    existing_profile.learning_state = _normalize_learning_state(item.get("learning_state"), default_state="stable")
                elif not current_learning_state:
                    existing_profile.learning_state = _normalize_learning_state(None, default_state="stable")
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
                    repair_attempts=int(item["repair_attempts"] if item.get("repair_attempts") is not None else 2),
                    repair_strategies=list(item.get("repair_strategies") or []),
                    preferred_prompt_version_id=item.get("preferred_prompt_version_id"),
                    output_contract_strictness=str(item.get("output_contract_strictness") or "repair_required"),
                    supports_json_mode=bool(item.get("supports_json_mode", False)),
                    requires_english_prompt=bool(item.get("requires_english_prompt", False)),
                    learning_state=_normalize_learning_state(item.get("learning_state"), default_state="stable"),
                    notes=_normalize_notes(item.get("notes")),
                    enabled=bool(item.get("enabled", True)),
                ))

    def update_learning_state(
        self,
        profile: PlanningModelProfileDB,
        *,
        state: str,
        source: str = "planning_model_profile_service",
        observed_output_format: str | None = None,
        observed_model_family: str | None = None,
        prompt_version_id: str | None = None,
        sample_size: int | None = None,
        reason_codes: list[str] | None = None,
    ) -> PlanningModelProfileDB:
        normalized = _normalize_learning_state(
            {
                "state": state,
                "source": source,
                "observed_output_format": observed_output_format,
                "observed_model_family": observed_model_family,
                "prompt_version_id": prompt_version_id,
                "sample_size": sample_size,
                "reason_codes": reason_codes or [],
                "updated_at": time.time(),
            },
            default_state="stable",
        )
        profile.learning_state = normalized
        if hasattr(profile, "__table__"):
            return get_repository_registry().planning_model_profile_repo.save(profile)
        return profile

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
            "learning_state": _normalize_learning_state(None, default_state="stable"),
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
            "repair_attempts": int(profile.repair_attempts if profile.repair_attempts is not None else 2),
            "repair_strategies": list(profile.repair_strategies or []),
            "preferred_prompt_version_id": profile.preferred_prompt_version_id,
            "output_contract_strictness": str(profile.output_contract_strictness or "repair_required"),
            "supports_json_mode": bool(profile.supports_json_mode),
            "requires_english_prompt": bool(profile.requires_english_prompt),
            "preferred_output_format": _extract_preferred_output_format(notes),
            "learning_state": _normalize_learning_state(getattr(profile, "learning_state", None), default_state="stable"),
            "notes": profile.notes,
        }


_SERVICE = PlanningModelProfileService()


def get_planning_model_profile_service() -> PlanningModelProfileService:
    return _SERVICE
