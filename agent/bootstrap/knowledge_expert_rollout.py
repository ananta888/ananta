"""Composition root for persistent Hub-owned knowledge-expert rollout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flask import Flask

from agent.config import settings
from agent.services.knowledge_expert_rollout_controller import KnowledgeExpertRolloutController


@dataclass(frozen=True, slots=True)
class KnowledgeExpertRolloutWiringStatus:
    ready: bool
    reason_code: str | None


def initialize_knowledge_expert_rollout(app: Flask) -> KnowledgeExpertRolloutWiringStatus:
    if str(app.config.get("ROLE") or "").strip().lower() != "hub":
        status = KnowledgeExpertRolloutWiringStatus(False, "knowledge_expert_rollout_hub_role_required")
    else:
        generation_switch = app.extensions.get("knowledge_expert_registry_service")
        if not callable(getattr(generation_switch, "switch", None)):
            status = KnowledgeExpertRolloutWiringStatus(False, "knowledge_expert_registry_unavailable")
        else:
            try:
                controller = KnowledgeExpertRolloutController(
                    Path(
                        str(
                            app.config.get("ANANTA_KNOWLEDGE_EXPERTS_ROLLOUT_STATE")
                            or settings.knowledge_experts_rollout_state
                        )
                    ),
                    generation_switch=generation_switch,
                )
            except (OSError, RuntimeError, ValueError):
                status = KnowledgeExpertRolloutWiringStatus(
                    False,
                    "knowledge_expert_rollout_configuration_invalid",
                )
            else:
                app.extensions["knowledge_expert_rollout_controller"] = controller
                status = KnowledgeExpertRolloutWiringStatus(True, None)
    app.extensions["knowledge_expert_rollout_wiring_status"] = status
    return status


__all__ = ["KnowledgeExpertRolloutWiringStatus", "initialize_knowledge_expert_rollout"]
