"""Tutorial system for the Ananta Operator TUI snake mode.

Tutorials are defined as YAML files in the tutorials/ directory.
Each tutorial has steps with completion events that the TUI fires.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

try:
    import yaml as _yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


_TUTORIALS_DIR = Path(__file__).parent / "tutorials"

_COMPLETION_EVENTS = frozenset({
    "any_key", "navigation_moved", "section_changed", "section_visited",
    "refresh_triggered", "command_executed", "snake_activated",
    "snake_deactivated", "snake_moved", "snake_paused", "snake_color_changed",
    "tutorial_toggled", "ask_command_used",
})


# ── loading ───────────────────────────────────────────────────────────────────


def _parse_yaml_safe(text: str) -> Any:
    if _HAS_YAML:
        return _yaml.safe_load(text)
    # minimal fallback: parse only the title line if yaml not available
    for line in text.splitlines():
        if line.startswith("title:"):
            return {"title": line[6:].strip().strip("\"'"), "steps": []}
    return {}


def load_tutorial(name: str) -> dict[str, Any] | None:
    """Load a tutorial by name. Returns None if not found or invalid."""
    path = _TUTORIALS_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        data = _parse_yaml_safe(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    if not data.get("title") or not isinstance(data.get("steps"), list):
        return None
    steps = []
    for step in data["steps"]:
        if not isinstance(step, dict) or not step.get("id"):
            continue
        steps.append({
            "id": str(step["id"]),
            "title": str(step.get("title") or step["id"]),
            "task": str(step.get("task") or ""),
            "hint": str(step.get("hint") or ""),
            "completion_event": str(step.get("completion_event") or "any_key"),
            "section": step.get("section"),
        })
    return {
        "name": name,
        "title": str(data["title"]),
        "description": str(data.get("description") or ""),
        "steps": steps,
        "step_count": len(steps),
    }


def list_tutorials() -> list[dict[str, Any]]:
    """Return metadata for all available tutorial YAML files."""
    if not _TUTORIALS_DIR.exists():
        return []
    result = []
    for path in sorted(_TUTORIALS_DIR.glob("*.yaml")):
        t = load_tutorial(path.stem)
        if t is not None:
            result.append({
                "name": t["name"],
                "title": t["title"],
                "description": t["description"],
                "step_count": t["step_count"],
            })
    return result


# ── runtime state ─────────────────────────────────────────────────────────────


def make_tutorial_state(name: str, *, start_step: int = 0) -> dict[str, Any] | None:
    """Create a fresh tutorial runtime state dict for storage in game state."""
    t = load_tutorial(name)
    if t is None:
        return None
    return {
        "name": name,
        "title": t["title"],
        "step_count": t["step_count"],
        "current_step": max(0, min(start_step, t["step_count"] - 1)),
        "started_at": time.monotonic(),
        "steps_skipped": 0,
        "active": True,
        "guided": False,
        "guided_section_at": 0.0,
    }


def get_current_step(tutorial_state: dict[str, Any]) -> dict[str, Any] | None:
    """Return current step data, or None if tutorial is done/inactive."""
    if not isinstance(tutorial_state, dict) or not tutorial_state.get("active"):
        return None
    name = str(tutorial_state.get("name") or "")
    idx = int(tutorial_state.get("current_step") or 0)
    t = load_tutorial(name)
    if t is None or idx >= len(t["steps"]):
        return None
    return t["steps"][idx]


def advance_step(tutorial_state: dict[str, Any], *, skipped: bool = False) -> dict[str, Any]:
    """Advance to next step. Returns updated state. Marks inactive if done."""
    state = dict(tutorial_state)
    idx = int(state.get("current_step") or 0)
    count = int(state.get("step_count") or 0)
    if skipped:
        state["steps_skipped"] = int(state.get("steps_skipped") or 0) + 1
    next_idx = idx + 1
    if next_idx >= count:
        state["active"] = False
        state["completed_at"] = time.monotonic()
    else:
        state["current_step"] = next_idx
    return state


def progress_bar(tutorial_state: dict[str, Any], *, width: int = 10) -> str:
    """Return ASCII progress bar string: ████░░░░░░"""
    idx = int(tutorial_state.get("current_step") or 0)
    count = max(1, int(tutorial_state.get("step_count") or 1))
    done = min(width, round(idx / count * width))
    return "█" * done + "░" * (width - done)


def format_step_header(tutorial_state: dict[str, Any]) -> str:
    """Return one-line header: [ Tutorial · Step 3/9 ████░░░░░░ ]"""
    if not isinstance(tutorial_state, dict) or not tutorial_state.get("active"):
        return ""
    idx = int(tutorial_state.get("current_step") or 0)
    count = int(tutorial_state.get("step_count") or 0)
    bar = progress_bar(tutorial_state)
    return f"[ Tutorial · Step {idx + 1}/{count} {bar} ]"


# ── completion check ──────────────────────────────────────────────────────────


def check_step_completion(step: dict[str, Any], fired_event: str) -> bool:
    """Return True if the given event satisfies the step's completion_event."""
    required = str(step.get("completion_event") or "any_key")
    if required == "any_key":
        return True
    return fired_event == required


# ── artifact record ───────────────────────────────────────────────────────────


def make_step_artifact(
    tutorial_state: dict[str, Any],
    step: dict[str, Any],
    *,
    operator: str = "local",
) -> dict[str, Any]:
    """Build an artifact dict for a completed tutorial step."""
    return {
        "type": "tutorial_step",
        "tutorial": str(tutorial_state.get("name") or ""),
        "step_id": str(step.get("id") or ""),
        "step_title": str(step.get("title") or ""),
        "operator": operator,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "steps_skipped": int(tutorial_state.get("steps_skipped") or 0),
    }


def make_completion_artifact(
    tutorial_state: dict[str, Any],
    *,
    operator: str = "local",
) -> dict[str, Any]:
    """Build an artifact dict for a fully completed tutorial."""
    started = float(tutorial_state.get("started_at") or 0)
    completed = float(tutorial_state.get("completed_at") or time.monotonic())
    return {
        "type": "tutorial_complete",
        "tutorial": str(tutorial_state.get("name") or ""),
        "title": str(tutorial_state.get("title") or ""),
        "operator": operator,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(completed - started, 1),
        "steps_skipped": int(tutorial_state.get("steps_skipped") or 0),
    }
