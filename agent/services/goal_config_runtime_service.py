from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from flask import current_app, has_app_context

from agent.services.repository_registry import get_repository_registry


@dataclass(frozen=True)
class GoalEffectiveConfig:
    config: dict[str, Any]
    source: str
    profile_id: str | None
    checksum: str | None


class GoalConfigRuntimeService:
    @staticmethod
    def _global_config() -> dict[str, Any]:
        if has_app_context():
            return dict(current_app.config.get("AGENT_CONFIG", {}) or {})
        return {}

    def get_effective_config(self, goal_id: str | None, task_id: str | None = None) -> GoalEffectiveConfig:
        _ = task_id
        if not goal_id:
            return GoalEffectiveConfig(
                config=self._global_config(),
                source="global_fallback",
                profile_id=None,
                checksum=None,
            )
        goal = get_repository_registry().goal_repo.get_by_id(str(goal_id))
        if goal is None:
            return GoalEffectiveConfig(
                config=self._global_config(),
                source="global_fallback",
                profile_id=None,
                checksum=None,
            )
        execution_preferences = dict(getattr(goal, "execution_preferences", None) or {})
        snapshot = dict(execution_preferences.get("config_snapshot") or {})
        scoped_config = dict(snapshot.get("config") or {}) if isinstance(snapshot, dict) else {}
        if not scoped_config:
            return GoalEffectiveConfig(
                config=self._global_config(),
                source="global_fallback",
                profile_id=str(execution_preferences.get("config_profile") or "").strip() or None,
                checksum=str(execution_preferences.get("config_snapshot_checksum") or "").strip() or None,
            )
        return GoalEffectiveConfig(
            config=scoped_config,
            source="snapshot",
            profile_id=str(snapshot.get("profile_id") or execution_preferences.get("config_profile") or "").strip() or None,
            checksum=str(execution_preferences.get("config_snapshot_checksum") or "").strip() or None,
        )


_SERVICE = GoalConfigRuntimeService()


def get_goal_config_runtime_service() -> GoalConfigRuntimeService:
    return _SERVICE
