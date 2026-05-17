from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from agent.services.config_profile_service import get_config_profile_service

_ALLOWED_TOP_LEVEL_KEYS = {
    "default_provider",
    "default_model",
    "llm_config",
    "opencode_runtime",
    "worker_runtime",
    "sgpt_routing",
    "task_kind_model_overrides",
    "role_model_overrides",
    "template_model_overrides",
    "planning_policy",
    "llm_profile_policy",
    "task_kind_execution_policies",
    "worker_selection",
}

_SECRET_KEY_MARKERS = ("key", "token", "secret", "password")


@dataclass(frozen=True)
class GoalConfigResolution:
    config_snapshot: dict[str, Any]
    provenance: dict[str, Any]
    checksum: str
    redaction_summary: dict[str, Any]


class GoalConfigResolverService:
    def resolve(
        self,
        *,
        system_config: dict[str, Any],
        profile_id: str | None = None,
        goal_overrides: dict[str, Any] | None = None,
        task_overrides: dict[str, Any] | None = None,
    ) -> GoalConfigResolution:
        base = self._build_system_default_snapshot(system_config)
        profile_payload = self._resolve_profile(profile_id)
        profile_overrides = dict(profile_payload.get("overrides") or {}) if profile_payload else {}
        goal_filtered = self._filter_overrides(goal_overrides or {})
        task_filtered = self._filter_overrides(task_overrides or {})

        merged = copy.deepcopy(base)
        provenance: dict[str, Any] = {}
        self._apply_with_provenance(merged, profile_overrides, "profile", provenance)
        self._apply_with_provenance(merged, goal_filtered, "goal", provenance)
        self._apply_with_provenance(merged, task_filtered, "task", provenance)

        resolved_profile_id = str((profile_payload or {}).get("id") or "").strip() or None
        snapshot = {
            "version": "goal_config_snapshot.v1",
            "resolved_at": None,
            "profile_id": resolved_profile_id,
            "config": merged,
            "provenance": {
                "resolution_order": ["system_default", "profile", "goal", "task"],
                "field_sources": provenance,
            },
        }
        checksum = self._checksum(snapshot)
        redacted, redaction_summary = self.redact_snapshot(snapshot)
        return GoalConfigResolution(
            config_snapshot=redacted,
            provenance=dict(snapshot.get("provenance") or {}),
            checksum=checksum,
            redaction_summary=redaction_summary,
        )

    def redact_snapshot(self, snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        redaction_count = 0

        def _walk(value: Any):
            nonlocal redaction_count
            if isinstance(value, dict):
                out: dict[str, Any] = {}
                for k, v in value.items():
                    key = str(k)
                    if self._looks_secret_key(key):
                        if v not in (None, ""):
                            redaction_count += 1
                        out[key] = "***REDACTED***"
                    else:
                        out[key] = _walk(v)
                return out
            if isinstance(value, list):
                return [_walk(v) for v in value]
            return value

        redacted = _walk(dict(snapshot or {}))
        return redacted, {"redacted_fields": redaction_count}

    def _build_system_default_snapshot(self, system_config: dict[str, Any]) -> dict[str, Any]:
        source = dict(system_config or {})
        normalized: dict[str, Any] = {}
        for key in _ALLOWED_TOP_LEVEL_KEYS:
            if key in source:
                normalized[key] = copy.deepcopy(source[key])
        return normalized

    def _resolve_profile(self, profile_id: str | None) -> dict[str, Any] | None:
        if not str(profile_id or "").strip():
            return None
        return get_config_profile_service().get_profile(profile_id)

    def _filter_overrides(self, payload: dict[str, Any]) -> dict[str, Any]:
        filtered: dict[str, Any] = {}
        for key, value in dict(payload or {}).items():
            if key in _ALLOWED_TOP_LEVEL_KEYS:
                filtered[key] = copy.deepcopy(value)
        return filtered

    def _apply_with_provenance(self, target: dict[str, Any], patch: dict[str, Any], source: str, provenance: dict[str, Any], prefix: str = "") -> None:
        for key, value in patch.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                self._apply_with_provenance(target[key], value, source, provenance, prefix=path)
                continue
            target[key] = copy.deepcopy(value)
            provenance[path] = source

    @staticmethod
    def _checksum(snapshot: dict[str, Any]) -> str:
        encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _looks_secret_key(key: str) -> bool:
        lowered = str(key or "").strip().lower()
        return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


_SERVICE = GoalConfigResolverService()


def get_goal_config_resolver_service() -> GoalConfigResolverService:
    return _SERVICE
