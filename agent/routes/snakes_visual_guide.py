"""Visual-Guide functions for the ananta-visual snake session."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from .snakes_chat_helpers import _append_room_ai_message

# ── Visual snake session (ananta-visual) ───────────────────────────────────────
# VG-003: Per-snake state lives in agent.services.visual_guide.service._visual_state.
# We re-export it here for backward compat with tests/monkeypatches that reference
# _visual_state via this module.  The dict object is shared — mutations are reflected
# in both places because Python dicts are reference types.
def _get_visual_state_ref() -> dict:
    """Lazy accessor that avoids a circular import at module init time."""
    from agent.services.visual_guide.service import _visual_state as _vs
    return _vs

_VISUAL_THROTTLE_S: float = 25.0  # minimum seconds between visual replies
_VISUAL_SESSION_ID: str = "ananta-visual"  # tag for messages belonging to the visual snake session

# VG-053: ThreadPoolExecutor replaces daemon threads for visual guide calls
_VISUAL_GUIDE_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="visual-guide")


def _visual_session_settings() -> dict:
    """Read all predictive_guide_* settings from the ananta-visual session.

    Falls back to _DEFAULT_SESSION_SETTINGS values when the session is missing
    or a key is absent. Same read-path as _visual_session_log_deltas_only so
    conftest monkeypatches on get_manager are picked up automatically."""
    from client_surfaces.operator_tui.config.user_config_manager import get_manager
    from client_surfaces.operator_tui.chat_state import _DEFAULT_SESSION_SETTINGS
    defaults = {k: v for k, v in _DEFAULT_SESSION_SETTINGS.items() if k.startswith("predictive_guide_")}
    try:
        sessions = get_manager().load().get("chat_sessions") or []
        sess = next(
            (s for s in sessions if str(s.get("id") or "") == _VISUAL_SESSION_ID),
            None,
        )
        stored = dict((sess or {}).get("settings") or {})
        return {**defaults, **{k: v for k, v in stored.items() if k in defaults}}
    except Exception:
        return defaults


def _visual_session_log_deltas_only() -> bool:
    return bool(_visual_session_settings().get("predictive_guide_log_deltas_only", True))


def _append_visual_user_tick(*, ui_snapshot: str, snake_id: str = "") -> None:
    """Persist the incoming UI snapshot as a system message in the ananta-visual session
    so the user can later review what the visual snake observed.

    When the session has predictive_guide_log_deltas_only=True, also append
    a [ui-delta] system message containing the human-readable diff between
    the previous and current snapshot.

    VG-003: snake_id scopes the delta_snapshot state per-snake."""
    text = f"[ui-tick] {ui_snapshot}" if ui_snapshot else "[ui-tick] (leer)"
    _append_room_ai_message(
        text=text,
        session_id=_VISUAL_SESSION_ID,
        visibility="system",
        sender_id="browser",
        ui_snapshot=ui_snapshot,
    )
    # ── Delta log (optional, opt-in via session setting) ──────────────────
    if not ui_snapshot:
        return
    log_deltas = _visual_session_log_deltas_only()
    if log_deltas:
        # Per-snake delta baseline (VG-003)
        from agent.services.visual_guide.service import _get_visual_state
        state = _get_visual_state(snake_id) if snake_id else None
        prev_delta = (state["delta_snapshot"] if state else "") or ""
        try:
            from agent.services.snapshot_delta import diff_snapshots
            delta = diff_snapshots(prev_delta, ui_snapshot)
            if not delta.is_empty():
                delta_text = f"[ui-delta] {delta.as_compact_text()}"
                _append_room_ai_message(
                    text=delta_text,
                    session_id=_VISUAL_SESSION_ID,
                    visibility="system",
                    sender_id="browser",
                )
        except Exception as exc:  # never let the delta path break the raw tick
            logging.getLogger(__name__).debug("ananta-visual delta log failed: %s", exc)
    # Update per-snake delta baseline — separate from reply-throttle key
    if snake_id:
        from agent.services.visual_guide.service import _get_visual_state
        state = _get_visual_state(snake_id)
        state["delta_snapshot"] = ui_snapshot
        state["updated_at"] = time.time()


def _spawn_visual_reply(ui_snapshot: str, snake_id: str = "") -> None:
    """Background: generate a proactive guide response for the visual snake session.

    VG-003: snake_id scopes reply throttle state per-snake.
    VG-010/011: delegates to VisualGuideService which uses ModelInvocationService.

    When predictive_guide_enabled is False the call is a no-op.
    When predictive_guide_multi_candidates > 1 the LLM is asked to produce
    N alternative guide sequences and the answer is stored as:
        <primary bubble text>
        __CANDIDATES__: [{"label":"primary","bubble":"...","steps":[...]}, ...]
    Single-candidate mode keeps the legacy __GUIDE__: format."""
    from agent.services.visual_guide.service import _visual_guide_service
    _visual_guide_service.handle_ui_tick(
        snake_id=snake_id,
        ui_snapshot=ui_snapshot,
        route="",
        visible_waypoints=[],
    )


def _spawn_region_explain_reply(region_steps: list[dict], route: str, snake_id: str = "") -> None:
    """Background: generate AI explanations for each element the user selected.

    VG-010/011: delegates to VisualGuideService which uses ModelInvocationService.
    Builds a __GUIDE__: response with the original pixel coordinates from
    region_steps and AI-generated bubble texts, so the client can play
    the guide with real explanations instead of raw element labels."""
    from agent.services.visual_guide.service import _visual_guide_service
    _visual_guide_service.handle_region_explain(
        snake_id=snake_id,
        region_steps=region_steps,
        route=route,
    )
