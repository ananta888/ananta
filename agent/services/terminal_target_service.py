from __future__ import annotations

from typing import Any

from agent.config import settings
from agent.services.repository_registry import get_repository_registry


class TerminalTargetService:
    def list_targets(self, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        app_cfg = dict(cfg or {})
        registry = get_repository_registry()
        targets: list[dict[str, Any]] = []

        worker_enabled = bool(settings.terminal_worker_target_enabled)
        if worker_enabled:
            for agent in list(registry.agent_repo.get_all() or []):
                role = str(getattr(agent, "role", "") or "").strip().lower()
                if role != "worker":
                    continue
                targets.append(
                    {
                        "target_type": "worker",
                        "target_id": str(getattr(agent, "name", None) or getattr(agent, "url", "worker")),
                        "display_name": str(getattr(agent, "name", None) or getattr(agent, "url", "worker")),
                        "runtime_url": str(getattr(agent, "url", "") or ""),
                        "health_state": str(getattr(agent, "status", "unknown") or "unknown"),
                        "risk_class": "terminal_workspace_mutation",
                    }
                )

        if bool(settings.terminal_hub_target_enabled):
            targets.append(
                {
                    "target_type": "hub",
                    "target_id": str(app_cfg.get("AGENT_NAME") or settings.agent_name or "hub"),
                    "display_name": str(app_cfg.get("AGENT_NAME") or settings.agent_name or "hub"),
                    "runtime_url": str(app_cfg.get("AGENT_URL") or settings.agent_url or ""),
                    "health_state": "online",
                    "risk_class": "terminal_hub_runtime_access",
                }
            )

        if bool(settings.hub_can_be_worker) and bool(settings.terminal_hub_as_worker_target_enabled):
            targets.append(
                {
                    "target_type": "hub_as_worker",
                    "target_id": str(app_cfg.get("AGENT_NAME") or settings.agent_name or "hub-as-worker"),
                    "display_name": f"{str(app_cfg.get('AGENT_NAME') or settings.agent_name or 'hub')} (hub-as-worker)",
                    "runtime_url": str(app_cfg.get("AGENT_URL") or settings.agent_url or ""),
                    "health_state": "online",
                    "risk_class": "terminal_hub_runtime_access",
                }
            )

        return targets


_SERVICE = TerminalTargetService()


def get_terminal_target_service() -> TerminalTargetService:
    return _SERVICE
