"""VisualGuideTraceService — writes VisualGuide events into the ai_snake_trace_store."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from agent.adapters import visual_guide_route_bridge as _route_bridge

if TYPE_CHECKING:
    from agent.services.visual_guide.models import VisualGuideAction, VisualGuideRequest

_log = logging.getLogger(__name__)


class VisualGuideTraceService:
    """Writes VisualGuide Events into the existing ai_snake_trace_store."""

    def start_trace(self, request: "VisualGuideRequest") -> str:
        """Creates a trace entry and returns the trace_id."""
        try:
            store = _route_bridge.get_trace_store()
            trace_id = store.new_trace(
                snake_id=request.snake_id or None,
                session_id="ananta-visual",
            )
            # Record the initial request_received event
            rec = _route_bridge.trace_recorder(store, trace_id)
            rec.event(
                "request_received",
                "VisualGuide request received",
                status="running",
                summary=f"trigger={request.trigger_type} snake={request.snake_id}",
                details=request.to_dict(),
            )
            return trace_id
        except Exception as exc:
            _log.debug("visual_guide trace start_trace failed: %s", exc)
            return ""

    def emit(self, trace_id: str, event: str, data: dict | None = None) -> None:
        """Adds an event to the trace."""
        if not trace_id:
            return
        try:
            store = _route_bridge.get_trace_store()
            rec = _route_bridge.trace_recorder(store, trace_id)
            rec.event(
                event,
                event.replace("_", " ").capitalize(),
                status="completed",
                details=dict(data or {}),
            )
        except Exception as exc:
            _log.debug("visual_guide trace emit failed: %s", exc)

    def finish_trace(
        self,
        trace_id: str,
        *,
        success: bool,
        action: "VisualGuideAction | None" = None,
    ) -> None:
        """Marks the trace as completed or failed."""
        if not trace_id:
            return
        try:
            store = _route_bridge.get_trace_store()
            rec = _route_bridge.trace_recorder(store, trace_id)
            status = "completed" if success else "failed"
            details: dict = {}
            if action is not None:
                details["action"] = action.to_dict()
            rec.event(
                "action_sent" if success else "error",
                "VisualGuide trace finished",
                status=status,
                details=details if details else None,
            )
            store.complete_trace(trace_id, status=status)
        except Exception as exc:
            _log.debug("visual_guide trace finish_trace failed: %s", exc)
