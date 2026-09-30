"""Diff3 command handlers for the Ananta operator TUI.

Extracted from client_surfaces/operator_tui/commands.py (SPLIT-002). The
``:plan`` handlers moved to ``commands_plan``; ``handle_plan_command`` stays
importable from here. ``handle_diff3_command`` dispatches through
``_DIFF3_ACTIONS`` / ``_DIFF3_PANEL_ACTIONS`` on a frozen ``_Diff3Command``.
``dispatch_ai_diff_request`` is resolved through this module at call time
(tests patch it here).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent.artifacts.goal_artifact_service import GoalArtifactService
from client_surfaces.operator_tui.commands_plan import handle_plan_command  # noqa: F401 - re-exported
from client_surfaces.operator_tui.diff.ai_diff_dispatch import dispatch_ai_diff_request
from client_surfaces.operator_tui.diff.ai_diff_panel_state import build_ai_diff_panel_state, set_ai_diff_mode
from client_surfaces.operator_tui.diff.diff_engine import DiffEngine
from client_surfaces.operator_tui.diff.diff_source_resolver import DiffSourceResolver
from client_surfaces.operator_tui.diff.diff_sources import (
    build_current_diff_source_ref,
    build_output_artifact_source_ref,
)
from client_surfaces.operator_tui.diff.three_way_diff_state import (
    build_current_diff_three_panel_session,
    set_panel_state,
    validate_three_way_diff_session,
)
from client_surfaces.operator_tui.models import CommandResult, OperatorState, PanelState


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _get_diff3_state(state: OperatorState) -> dict:
    game = dict(state.header_logo_game or {})
    current = game.get("diff3_state")
    if isinstance(current, dict) and not validate_three_way_diff_session(current):
        return dict(current)
    return build_current_diff_three_panel_session(
        session_id="diff3-default",
        goal_id=str(game.get("active_goal_id") or "") or None,
    )


def _build_diff3_payload(*, state: dict, goal_id: str | None) -> dict:
    resolver = DiffSourceResolver(repo_root=Path.cwd(), goal_artifact_service=GoalArtifactService())
    engine = DiffEngine()
    summaries: list[dict[str, object]] = []
    for panel in list(state.get("panels") or []):
        panel_id = str(panel.get("panel_id") or "?")
        panel_type = str(panel.get("panel_type") or "empty")
        render_mode = str(panel.get("render_mode") or "")
        source = panel.get("source_left") if isinstance(panel.get("source_left"), dict) else None
        source_label = str((source or {}).get("display_name") or panel_type)
        status = "empty"
        stats: dict[str, object] = {}
        if source and panel_type == "diff":
            # A state/render refresh must stay cheap. Current working-tree diffs
            # are resolved only by workflows that consume their content (for
            # example the AI context builder), not for every TUI command.
            source_kind = str(source.get("source_kind") or "")
            if source_kind in {"git_diff", "working_tree"}:
                status = "available"
                resolved = None
            else:
                resolved = resolver.resolve(source, goal_id=goal_id)
            if resolved is not None and bool(resolved.get("ok")):
                status = "ready"
                doc = engine.build_document(left=resolved, render_mode=render_mode)
                stats = dict(doc.get("stats") or {})
                if str(source.get("source_kind") or "") == "goal_output_artifact":
                    output_ref = str(resolved.get("output_artifact_id") or "")
                    prov = str(resolved.get("provenance_id") or "")
                    source_label = f"{source_label}#{output_ref}" if output_ref else source_label
                    if prov:
                        source_label = f"{source_label} prov={prov}"
            elif resolved is not None:
                status = str(resolved.get("reason_code") or "degraded")
        elif panel_type.startswith("ai_"):
            ai_state = dict(dict(state.get("extensions") or {}).get("ai_panel_state") or {})
            status = str(ai_state.get("status") or "ready")
        summaries.append(
            {
                "panel_id": panel_id,
                "panel_type": panel_type,
                "source_label": source_label,
                "render_mode": render_mode,
                "status": status,
                "stats": stats,
                "filters": dict(panel.get("filters") or {}),
            }
        )
    return {
        "diff3_mode": True,
        "goal_id": goal_id,
        "active_panel": str(state.get("active_panel") or "A"),
        "sync_scroll": bool(dict(state.get("extensions") or {}).get("sync_scroll", False)),
        "ai_panel_state": dict(dict(state.get("extensions") or {}).get("ai_panel_state") or {}),
        "panel_summaries": summaries,
        "raw_state": state,
    }


def _state_with_diff3_payload(state: OperatorState, *, game: dict, diff3_state: dict) -> OperatorState:
    goal_id = str(game.get("active_goal_id") or "").strip() or None
    game["diff3_state"] = diff3_state
    payload = _build_diff3_payload(state=diff3_state, goal_id=goal_id)
    section_payloads = dict(state.section_payloads or {})
    section_payloads["artifacts"] = payload
    panel_states = dict(state.panel_states or {})
    panel_states["artifacts"] = PanelState.HEALTHY
    return state.with_updates(
        header_logo_game=game,
        section_id="artifacts",
        selected_index=0,
        section_payloads=section_payloads,
        panel_states=panel_states,
    )


_DIFF3_PANEL_IDS = frozenset({"A", "B", "C"})
_DIFF3_AI_MODES = frozenset({"review", "explain", "risk", "tests", "patch", "chat"})
_DIFF3_FILTER_KEYS = frozenset({"path_filter", "status_filter", "hunk_filter", "search_text"})
_DIFF3_AI_PANEL_TYPES = {
    "review": "ai_review",
    "chat": "ai_review",
    "explain": "ai_explain",
    "risk": "ai_review",
    "tests": "ai_review",
    "patch": "ai_patch",
}
_DIFF3_SCROLL_STEPS = {"up": -1, "down": 1, "pageup": -20, "pagedown": 20}
_DIFF3_PANEL_USAGE = "diff3 panel <A|B|C> current|output|ai|mode|filter ..."
_DIFF3_OUTPUT_USAGE = "diff3 panel <A|B|C> output <output-artifact-id>"


@dataclass(frozen=True)
class _Diff3Command:
    """One ``:diff3`` invocation: arguments, TUI state, game copy and the diff3 session."""

    args: list[str]
    state: OperatorState
    game: dict
    diff3_state: dict

    def fail(self, message: str) -> CommandResult:
        return CommandResult(self.state, message, handled=False)

    def finish(self, diff3_state: dict, status_message: str, output: str | None = None, **kw) -> CommandResult:
        """Store ``diff3_state``, rebuild the artifacts payload and report ``status_message``."""
        next_state = _state_with_diff3_payload(self.state, game=self.game, diff3_state=diff3_state)
        if output is None:
            output = json.dumps(next_state.section_payloads["artifacts"], ensure_ascii=False)
        rendered = output
        return CommandResult(next_state.with_updates(status_message=status_message), rendered, **kw)


def _diff3_panel(diff3_state: dict, panel_id: str) -> dict | None:
    return next(
        (item for item in list(diff3_state.get("panels") or []) if str(item.get("panel_id") or "") == panel_id), None
    )


def _diff3_active_goal(game: dict) -> str | None:
    return str(game.get("active_goal_id") or "").strip() or None


# --- :diff3 panel <id> ... --------------------------------------------------------


def _panel_current(cmd: _Diff3Command, panel_id: str) -> dict | CommandResult:
    mode = "unified"
    for idx, token in enumerate(cmd.args):
        if str(token).lower() == "--mode" and idx + 1 < len(cmd.args):
            mode = str(cmd.args[idx + 1]).strip().lower()
    return set_panel_state(
        cmd.diff3_state,
        panel_id=panel_id,
        panel_type="diff",
        source_left=build_current_diff_source_ref(),
        source_right=None,
        render_mode=mode,
    )


def _panel_output(cmd: _Diff3Command, panel_id: str) -> dict | CommandResult:
    output_id = str(cmd.args[3]).strip() if len(cmd.args) >= 4 else ""
    if not output_id:
        return cmd.fail(_DIFF3_OUTPUT_USAGE)
    return set_panel_state(
        cmd.diff3_state,
        panel_id=panel_id,
        panel_type="diff",
        source_left=build_output_artifact_source_ref(
            output_artifact_id=output_id, goal_id=_diff3_active_goal(cmd.game)
        ),
        source_right=None,
        render_mode="unified",
    )


def _panel_ai(cmd: _Diff3Command, panel_id: str) -> dict | CommandResult:
    mode = str(cmd.args[3]).lower() if len(cmd.args) > 3 else "review"
    panel_type = _DIFF3_AI_PANEL_TYPES.get(mode)
    if panel_type is None:
        return cmd.fail(f"invalid ai mode: {mode}")
    diff3_state = set_panel_state(
        cmd.diff3_state,
        panel_id=panel_id,
        panel_type=panel_type,
        source_left=None,
        source_right=None,
        render_mode="ai_chat" if mode == "chat" else "ai_review",
    )
    extensions = dict(diff3_state.get("extensions") or {})
    extensions["ai_panel_state"] = build_ai_diff_panel_state(mode=mode, selected_panels=["A", "B"], status="idle")
    diff3_state["extensions"] = extensions
    return diff3_state


def _panel_mode(cmd: _Diff3Command, panel_id: str) -> dict | CommandResult:
    if len(cmd.args) < 4:
        return cmd.fail("diff3 panel <A|B|C> mode <render-mode>")
    mode = str(cmd.args[3]).lower()
    panel = _diff3_panel(cmd.diff3_state, panel_id)
    if panel is None:
        return cmd.fail(f"panel not found: {panel_id}")
    return set_panel_state(
        cmd.diff3_state,
        panel_id=panel_id,
        panel_type=str(panel.get("panel_type") or "diff"),
        source_left=panel.get("source_left"),
        source_right=panel.get("source_right"),
        render_mode=mode,
    )


def _panel_filter(cmd: _Diff3Command, panel_id: str) -> dict | CommandResult:
    panel = _diff3_panel(cmd.diff3_state, panel_id)
    if panel is None:
        return cmd.fail(f"panel not found: {panel_id}")
    filters = dict(panel.get("filters") or {})
    for token in cmd.args[3:]:
        value = str(token).strip()
        if "=" not in value:
            continue
        key, val = value.split("=", 1)
        if key.strip() in _DIFF3_FILTER_KEYS:
            filters[key.strip()] = val.strip()
    panel["filters"] = filters
    cmd.diff3_state["updated_at"] = _now_iso()
    return cmd.diff3_state


_DIFF3_PANEL_ACTIONS = {
    "current": _panel_current,
    "output": _panel_output,
    "ai": _panel_ai,
    "mode": _panel_mode,
    "filter": _panel_filter,
}


def _diff3_panel_command(cmd: _Diff3Command) -> CommandResult:
    if len(cmd.args) < 3:
        return cmd.fail(_DIFF3_PANEL_USAGE)
    panel_id = str(cmd.args[1]).upper()
    if panel_id not in _DIFF3_PANEL_IDS:
        return cmd.fail(f"invalid panel id: {panel_id}")
    handler = _DIFF3_PANEL_ACTIONS.get(str(cmd.args[2]).lower())
    if handler is None:
        return cmd.fail(_DIFF3_PANEL_USAGE)
    updated = handler(cmd, panel_id)
    if isinstance(updated, CommandResult):
        return updated
    return cmd.finish(updated, f"diff3 panel {panel_id} updated")


# --- focus, sync, scroll ------------------------------------------------------------


def _diff3_focus(cmd: _Diff3Command) -> CommandResult:
    panel_id = str(cmd.args[1]).upper() if len(cmd.args) > 1 else ""
    if panel_id not in _DIFF3_PANEL_IDS:
        return cmd.fail("diff3 focus <A|B|C>")
    cmd.diff3_state["active_panel"] = panel_id
    return cmd.finish(cmd.diff3_state, f"diff3 focus {panel_id}", f"diff3 focus {panel_id}")


def _diff3_sync(cmd: _Diff3Command) -> CommandResult:
    flag = str(cmd.args[1]).lower() if len(cmd.args) > 1 else ""
    if flag not in {"on", "off"}:
        return cmd.fail("diff3 sync on|off")
    extensions = dict(cmd.diff3_state.get("extensions") or {})
    extensions["sync_scroll"] = flag == "on"
    cmd.diff3_state["extensions"] = extensions
    return cmd.finish(cmd.diff3_state, f"diff3 sync {flag}", f"diff3 sync {flag}")


def _diff3_scroll(cmd: _Diff3Command) -> CommandResult:
    direction = str(cmd.args[1]).lower() if len(cmd.args) > 1 else ""
    step = _DIFF3_SCROLL_STEPS.get(direction)
    if step is None:
        return cmd.fail("diff3 scroll up|down|pageup|pagedown")
    active = str(cmd.diff3_state.get("active_panel") or "A")
    panel = _diff3_panel(cmd.diff3_state, active)
    if panel is None:
        return cmd.fail(f"panel not found: {active}")
    scroll = dict(panel.get("scroll_state") or {})
    scroll["line"] = max(0, int(scroll.get("line") or 0) + step)
    panel["scroll_state"] = scroll
    cmd.diff3_state["updated_at"] = _now_iso()
    return cmd.finish(cmd.diff3_state, f"diff3 scroll {direction}", f"diff3 scroll {direction}")


# --- :diff3 ai ... -------------------------------------------------------------------


def _degraded_ai_diff_result(run_mode: str, reason_code: str, summary: str) -> dict:
    return {
        "status": "degraded",
        "reason_code": reason_code,
        "response": {
            "schema": "ai_diff_response.v1",
            "status": "degraded",
            "artifact_type": run_mode,
            "summary": summary,
            "findings": [],
            "risks": [],
            "suggested_tests": [],
            "patch_suggestions": [],
            "source_refs": [],
            "reason_code": reason_code,
        },
        "context_envelope": {},
        "provenance_id": "",
        "output_artifact_id": "",
    }


def _dispatch_ai_diff(game: dict, diff3_state: dict, run_mode: str) -> dict:
    """Run the AI diff request; timeouts and failures become degraded results."""
    try:
        return dispatch_ai_diff_request(goal_id=_diff3_active_goal(game), diff3_state=diff3_state, mode=run_mode)
    except TimeoutError:
        return _degraded_ai_diff_result(run_mode, "ai_diff_timeout", "AI diff request timed out")
    except Exception:
        return _degraded_ai_diff_result(run_mode, "ai_diff_dispatch_failed", "AI diff dispatch failed")


def _completed_ai_panel_state(running: dict, run_mode: str, result: dict, status_label: str) -> dict:
    completed = set_ai_diff_mode(running, mode=run_mode, status=status_label)
    completed["last_response_ref"] = str(result.get("output_artifact_id") or result.get("provenance_id") or "")
    context_hash = hashlib.sha1(
        json.dumps(result.get("context_envelope") or {}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    completed["context_refs"] = [f"ctx:{context_hash}"]
    completed["selected_hunks"] = list((result.get("context_envelope") or {}).get("selected_hunk_refs") or [])
    return completed


def _diff3_ai_run(cmd: _Diff3Command) -> CommandResult:
    diff3_state = cmd.diff3_state
    extensions = dict(diff3_state.get("extensions") or {})
    current_ai = dict(extensions.get("ai_panel_state") or {})
    run_mode = str(cmd.args[2]).lower() if len(cmd.args) > 2 else str(current_ai.get("mode") or "review")
    if run_mode not in _DIFF3_AI_MODES:
        return cmd.fail("diff3 ai run [review|explain|risk|tests|patch|chat]")
    running = (
        set_ai_diff_mode(current_ai, mode=run_mode, status="running")
        if current_ai
        else build_ai_diff_panel_state(mode=run_mode, selected_panels=["A", "B"], status="running")
    )
    extensions["ai_panel_state"] = running
    diff3_state["extensions"] = extensions
    result = _dispatch_ai_diff(cmd.game, diff3_state, run_mode)
    status_label = "degraded" if str(result.get("status") or "") != "success" else "completed"
    extensions["ai_panel_state"] = _completed_ai_panel_state(running, run_mode, result, status_label)
    extensions["ai_last_response"] = dict(result.get("response") or {})
    extensions["ai_last_context"] = dict(result.get("context_envelope") or {})
    extensions["ai_last_findings"] = list(dict(result.get("response") or {}).get("findings") or [])
    diff3_state["extensions"] = extensions
    return cmd.finish(
        diff3_state,
        f"diff3 ai run {run_mode} {status_label}",
        json.dumps(result, ensure_ascii=False),
        handled=True,
    )


def _diff3_ai(cmd: _Diff3Command) -> CommandResult:
    mode = str(cmd.args[1]).lower() if len(cmd.args) > 1 else ""
    if mode == "run":
        return _diff3_ai_run(cmd)
    if mode not in _DIFF3_AI_MODES:
        return cmd.fail("diff3 ai review|explain|risk|tests|patch|chat|run [mode]")
    extensions = dict(cmd.diff3_state.get("extensions") or {})
    current_ai = extensions.get("ai_panel_state")
    if isinstance(current_ai, dict):
        extensions["ai_panel_state"] = set_ai_diff_mode(current_ai, mode=mode, status="idle")
    else:
        extensions["ai_panel_state"] = build_ai_diff_panel_state(mode=mode, selected_panels=["A", "B"], status="idle")
    cmd.diff3_state["extensions"] = extensions
    return cmd.finish(cmd.diff3_state, f"diff3 ai {mode}", f"diff3 ai {mode}")


_DIFF3_ACTIONS = {
    "panel": _diff3_panel_command,
    "focus": _diff3_focus,
    "sync": _diff3_sync,
    "scroll": _diff3_scroll,
    "ai": _diff3_ai,
}


def handle_diff3_command(args: list[str], state: OperatorState) -> CommandResult:
    """Dispatch :diff3 subcommands."""
    cmd = _Diff3Command(
        args=args, state=state, game=dict(state.header_logo_game or {}), diff3_state=_get_diff3_state(state)
    )
    if not args:
        return cmd.finish(cmd.diff3_state, "diff3 opened")
    handler = _DIFF3_ACTIONS.get(str(args[0]).lower())
    if handler is None:
        return cmd.fail("diff3: panel ... | focus <A|B|C> | scroll ... | sync on|off | ai ...")
    return handler(cmd)
