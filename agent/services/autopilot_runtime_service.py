from __future__ import annotations

from flask import current_app

from agent.config import settings
from agent.repository import team_repo


class AutopilotRuntimeService:
    """Thin runtime facade around the hub-owned autonomous loop singleton."""

    def _loop(self):
        from agent.routes.tasks.autopilot import autonomous_loop

        return autonomous_loop

    def status(self) -> dict:
        return self._loop().status()

    def start(
        self,
        *,
        interval_seconds=None,
        max_concurrency=None,
        goal=None,
        team_id=None,
        budget_label=None,
        security_level=None,
    ) -> dict:
        resolved_team_id = team_id
        if not resolved_team_id:
            active = next((t for t in team_repo.get_all() if bool(getattr(t, "is_active", False))), None)
            if active is not None:
                resolved_team_id = active.id
        self._loop().start(
            interval_seconds=interval_seconds,
            max_concurrency=max_concurrency,
            goal=goal,
            team_id=resolved_team_id,
            budget_label=budget_label,
            security_level=security_level,
            persist=True,
            background=not bool(current_app.testing),
        )
        return self.status()

    def stop(self) -> dict:
        self._loop().stop(persist=True)
        return self.status()

    def tick(self, *, requested_team_id: str | None = None) -> dict:
        loop = self._loop()
        if requested_team_id:
            loop.team_id = requested_team_id
        elif not loop.team_id:
            active = next((t for t in team_repo.get_all() if bool(getattr(t, "is_active", False))), None)
            if active is not None:
                loop.team_id = active.id
        result = loop.tick_once()
        return {**loop.status(), **result}

    def circuit_status(self) -> dict:
        return self._loop().circuit_status()

    def reset_circuits(self, *, worker_url: str | None = None) -> dict:
        return self._loop().reset_circuits(worker_url=worker_url)

    def is_hub_allowed(self) -> bool:
        return settings.role == "hub"


autopilot_runtime_service = AutopilotRuntimeService()


def get_autopilot_runtime_service() -> AutopilotRuntimeService:
    return autopilot_runtime_service
