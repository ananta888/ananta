"""Persistence helpers for snake highscore and tutor configuration.

Config directory: ~/.config/ananta/
  snake_scores.json   – highscore, last score, game count
  tutor_config.json   – tutor depth mode, visited sections, tutorial progress
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    return Path.home() / ".config" / "ananta"


# ── highscore ─────────────────────────────────────────────────────────────────


def load_snake_scores() -> dict[str, Any]:
    path = _config_dir() / "snake_scores.json"
    if not path.exists():
        return {"high": 0, "last": 0, "games": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"high": 0, "last": 0, "games": 0}
    except Exception:
        return {"high": 0, "last": 0, "games": 0}


def save_snake_score(score: int) -> dict[str, Any]:
    """Persist a finished game score. Returns updated scores dict."""
    prev = load_snake_scores()
    updated: dict[str, Any] = {
        "high": max(int(prev.get("high") or 0), score),
        "last": score,
        "games": int(prev.get("games") or 0) + 1,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path = _config_dir() / "snake_scores.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(updated, indent=2), encoding="utf-8")
    except Exception:
        pass
    return updated


# ── tutor config ──────────────────────────────────────────────────────────────

_DEFAULT_TUTOR_CFG: dict[str, Any] = {
    "mode": "overview",
    "silent": False,
    "visited_sections": [],
    "tutorial_progress": {},
}


def load_tutor_config() -> dict[str, Any]:
    path = _config_dir() / "tutor_config.json"
    if not path.exists():
        return dict(_DEFAULT_TUTOR_CFG)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = dict(_DEFAULT_TUTOR_CFG)
        if isinstance(data, dict):
            cfg.update(data)
        return cfg
    except Exception:
        return dict(_DEFAULT_TUTOR_CFG)


def save_tutor_config(cfg: dict[str, Any]) -> None:
    path = _config_dir() / "tutor_config.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def get_tutor_mode() -> str:
    """Return current tutor depth mode: overview | deep | expert."""
    return str(load_tutor_config().get("mode") or "overview")


def set_tutor_mode(mode: str) -> None:
    if mode not in {"overview", "deep", "expert"}:
        return
    cfg = load_tutor_config()
    cfg["mode"] = mode
    save_tutor_config(cfg)


def is_tutor_silent() -> bool:
    return bool(load_tutor_config().get("silent"))


def set_tutor_silent(silent: bool) -> None:
    cfg = load_tutor_config()
    cfg["silent"] = silent
    save_tutor_config(cfg)


def mark_section_visited(section_id: str) -> bool:
    """Mark a section as visited. Returns True if this was the first visit."""
    cfg = load_tutor_config()
    visited: list[str] = list(cfg.get("visited_sections") or [])
    if section_id in visited:
        return False
    visited.append(section_id)
    cfg["visited_sections"] = visited
    save_tutor_config(cfg)
    return True


def get_visited_sections() -> list[str]:
    return list(load_tutor_config().get("visited_sections") or [])


# ── tutorial progress ─────────────────────────────────────────────────────────


def save_tutorial_progress(tutorial_name: str, step_idx: int) -> None:
    cfg = load_tutor_config()
    progress: dict[str, Any] = dict(cfg.get("tutorial_progress") or {})
    progress[tutorial_name] = {"step": step_idx, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    cfg["tutorial_progress"] = progress
    save_tutor_config(cfg)


def get_tutorial_progress(tutorial_name: str) -> int:
    """Return last completed step index (0-based), or -1 if not started."""
    cfg = load_tutor_config()
    progress = cfg.get("tutorial_progress") or {}
    entry = progress.get(tutorial_name)
    if isinstance(entry, dict):
        return int(entry.get("step") or 0)
    return -1


def reset_tutorial_progress(tutorial_name: str) -> None:
    cfg = load_tutor_config()
    progress = dict(cfg.get("tutorial_progress") or {})
    progress.pop(tutorial_name, None)
    cfg["tutorial_progress"] = progress
    save_tutor_config(cfg)
