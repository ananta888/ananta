from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from agent.services.config_profile_service import get_config_profile_service

ALLOWED_GOAL_CONFIG_KEYS: frozenset[str] = frozenset({
    "feature_flags",
    "hermes_worker_adapter",
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
    "git_workspace",
    "workspace_context_policy",
    "llm_tool_guardrails",
})

# Backward-compatible alias — internal code should migrate to ALLOWED_GOAL_CONFIG_KEYS.
_ALLOWED_TOP_LEVEL_KEYS = ALLOWED_GOAL_CONFIG_KEYS

_SECRET_KEY_MARKERS = ("key", "token", "secret", "password", "authorization", "credential", "bearer")
_NON_SECRET_KEY_EXCEPTIONS: frozenset[str] = frozenset({
    "max_output_tokens",
    "max_tokens_per_request",
    "chars_per_token_estimate",
})

# These nested config paths are treated as atomic replacements rather than deep-merged.
# When a profile or goal override sets one of these keys, the entire dict replaces the
# base config value — partial merging would produce confusing hybrid routing tables.
_REPLACE_NOT_MERGE_PATHS: frozenset[str] = frozenset({
    "sgpt_routing.task_kind_backend",
    "sgpt_routing.research_capability_backend",
    "sgpt_routing.backend_parallel_limits",
    "planning_policy.validation_profiles",
})


@dataclass(frozen=True)
class GoalConfigResolution:
    config_snapshot: dict[str, Any]
    provenance: dict[str, Any]
    checksum: str
    redaction_summary: dict[str, Any]
    unknown_keys: tuple[str, ...]


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
        goal_filtered, goal_unknown = self._filter_overrides(goal_overrides or {})
        task_filtered, task_unknown = self._filter_overrides(task_overrides or {})
        unknown_keys: tuple[str, ...] = tuple(sorted(set(goal_unknown) | set(task_unknown)))

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
        # GSC-003: checksum is computed over the redacted snapshot so that
        # secret values never influence the stored hash.
        redacted, redaction_summary = self.redact_snapshot(snapshot)
        checksum = self._checksum(redacted)
        return GoalConfigResolution(
            config_snapshot=redacted,
            provenance=dict(snapshot.get("provenance") or {}),
            checksum=checksum,
            redaction_summary=redaction_summary,
            unknown_keys=unknown_keys,
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
        for key in ALLOWED_GOAL_CONFIG_KEYS:
            if key in source:
                normalized[key] = copy.deepcopy(source[key])
        return normalized

    def _resolve_profile(self, profile_id: str | None) -> dict[str, Any] | None:
        if not str(profile_id or "").strip():
            return None
        return get_config_profile_service().get_profile(profile_id)

    def _filter_overrides(self, payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        filtered: dict[str, Any] = {}
        unknown: list[str] = []
        for key, value in dict(payload or {}).items():
            if key in ALLOWED_GOAL_CONFIG_KEYS:
                filtered[key] = copy.deepcopy(value)
            else:
                unknown.append(key)
        return filtered, unknown

    def _apply_with_provenance(self, target: dict[str, Any], patch: dict[str, Any], source: str, provenance: dict[str, Any], prefix: str = "") -> None:
        for key, value in patch.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if path not in _REPLACE_NOT_MERGE_PATHS and isinstance(value, dict) and isinstance(target.get(key), dict):
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
        if lowered in _NON_SECRET_KEY_EXCEPTIONS:
            return False
        return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


_SERVICE = GoalConfigResolverService()


def get_goal_config_resolver_service() -> GoalConfigResolverService:
    return _SERVICE
