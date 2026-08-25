"""Permission-neutral role overlay projection for task prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from agent.services.cognitive_style_service import CognitiveStyleService


@dataclass(frozen=True, slots=True)
class AppliedStyleOverlay:
    rendered_system_prompt: str | None
    role_id: str
    overlay_id: str | None
    applied: bool
    permission_delta: str = "none"


class CognitiveStyleOverlayService:
    _TASK_ROLE = {
        "coding": "implementer", "implementation": "implementer",
        "debugging": "implementer", "review": "reviewer",
        "verification": "verifier", "testing": "qa", "qa": "qa",
        "research": "researcher", "architecture": "architect",
        "planning": "planner", "challenge": "challenger",
    }
    _ROLE_ALIAS = {"coder": "implementer", "reasoning": "reviewer"}

    def __init__(self, styles: CognitiveStyleService) -> None:
        self._styles = styles

    def apply(
        self,
        *,
        task: Mapping[str, object],
        system_prompt: str | None,
        fallback_role: str | None = None,
    ) -> AppliedStyleOverlay:
        role_id = self._resolve_role(task, fallback_role)
        configuration = self._styles.read().configuration
        overlay = next(
            (
                item for item in configuration.overlays
                if item.role_id == role_id and item.enabled
            ),
            None,
        )
        if overlay is None:
            return AppliedStyleOverlay(system_prompt, role_id, None, False)
        block = (
            f"[Operatives Rollen-Style-Overlay: {overlay.overlay_id}]\n"
            f"{overlay.instruction}\n"
            "Berechtigungsgrenze: Dieses Overlay verändert keine Rechte, Tool-Scope- oder "
            "Hub-Policy; zusätzliche Aktionen bleiben freigabepflichtig."
        )
        rendered = f"{system_prompt.strip()}\n\n{block}" if system_prompt else block
        return AppliedStyleOverlay(
            rendered_system_prompt=rendered,
            role_id=role_id,
            overlay_id=overlay.overlay_id,
            applied=True,
        )

    def _resolve_role(
        self,
        task: Mapping[str, object],
        fallback_role: str | None,
    ) -> str:
        explicit = str(
            task.get("role_id") or task.get("role") or task.get("agent_role") or ""
        ).strip().lower()
        if explicit:
            return self._ROLE_ALIAS.get(explicit, explicit)
        task_kind = str(task.get("task_kind") or task.get("type") or "").strip().lower()
        if task_kind in self._TASK_ROLE:
            return self._TASK_ROLE[task_kind]
        fallback = str(fallback_role or "").strip().lower()
        return self._ROLE_ALIAS.get(fallback, fallback) or "chat"


def apply_cognitive_style_overlay(
    *,
    task: Mapping[str, object],
    system_prompt: str | None,
    fallback_role: str | None = None,
) -> AppliedStyleOverlay:
    from agent.services.cognitive_style_service import get_cognitive_style_service

    try:
        return CognitiveStyleOverlayService(get_cognitive_style_service()).apply(
            task=task,
            system_prompt=system_prompt,
            fallback_role=fallback_role,
        )
    except Exception:
        return AppliedStyleOverlay(
            rendered_system_prompt=system_prompt,
            role_id=str(fallback_role or "chat"),
            overlay_id=None,
            applied=False,
        )


__all__ = [
    "AppliedStyleOverlay", "CognitiveStyleOverlayService",
    "apply_cognitive_style_overlay",
]
