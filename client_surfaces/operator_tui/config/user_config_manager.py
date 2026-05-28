"""UserConfigManager — atomic per-user and per-project AI-Snake config persistence.

Paths:
  global : ~/.anana/user.json           (user-wide defaults)
  project: <CWD>/user.json              (project-specific overrides)

Load order: defaults → global → project  (project wins)
Write     : always writes project file; also updates global on explicit flush.
Atomic    : writes to .user.json.tmp then os.replace() (POSIX-atomic).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

SCHEMA_VERSION = "user_config.v1"

_SCHEMA_KEYS: frozenset[str] = frozenset({
    # Visual AI-Snake
    "tutorial_mode",
    "ai_snake_provider_preference",
    "ai_visual_use_codecompass",
    # Chat panel
    "chat_panel_open",
    # Chat backend
    "chat_backend",
    "chat_backend_model",
    "chat_backend_api_base",
    "chat_ask_timeout_s",
    # CodeCompass / RAG
    "chat_use_codecompass",
    "chat_include_local_project",
    "chat_include_wikipedia",
    "chat_source_pack_id",
    # Context budgets
    "chat_context_chars",
    "chat_max_tokens",
    "chat_rag_top_k",
    "chat_answer_chars",
    # Memory (CMW track)
    "chat_use_history",
    "chat_history_turns",
    "chat_history_chars",
    "chat_use_summary",
    "chat_summary_chars",
    "chat_summary_update_every_turns",
    "chat_pass_memory_to_worker",
    "chat_worker_mode",
    "chat_backend_fallback",
    "chat_include_runtime_status",
})

_DEFAULTS: dict[str, Any] = {
    "tutorial_mode": False,
    "ai_snake_provider_preference": "lmstudio",
    "ai_visual_use_codecompass": False,
    "chat_panel_open": True,
    "chat_backend": "ananta-worker",
    "chat_backend_model": "",
    "chat_backend_api_base": "http://localhost:1234/v1",
    "chat_ask_timeout_s": 45.0,
    "chat_use_codecompass": True,
    "chat_include_local_project": True,
    "chat_include_wikipedia": False,
    "chat_source_pack_id": "ananta-dev-default",
    "chat_context_chars": 3000,
    "chat_max_tokens": 400,
    "chat_rag_top_k": 24,
    "chat_answer_chars": 6000,
    "chat_use_history": True,
    "chat_history_turns": 6,
    "chat_history_chars": 1800,
    "chat_use_summary": True,
    "chat_summary_chars": 1500,
    "chat_summary_update_every_turns": 3,
    "chat_pass_memory_to_worker": True,
    "chat_worker_mode": "snake_ask",
    "chat_backend_fallback": "lmstudio",
    "chat_include_runtime_status": False,
}


def global_config_path() -> Path:
    return Path.home() / ".anana" / "user.json"


def project_config_path(cwd: Path | None = None) -> Path:
    base = (cwd or Path.cwd()).resolve()
    return base / "user.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            return {}
        settings = data.get("settings")
        return dict(settings) if isinstance(settings, dict) else {}
    except (OSError, PermissionError, json.JSONDecodeError, ValueError) as exc:
        _log.warning("UserConfigManager: cannot read %s: %s", path, exc)
        return {}


def _write_atomic(path: Path, settings: dict[str, Any]) -> bool:
    """Write settings atomically using a temp-file + os.replace."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "settings": _validated(settings),
    }
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError as exc:
        _log.warning("UserConfigManager: write failed for %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _validated(settings: dict[str, Any]) -> dict[str, Any]:
    """Strip unknown keys and coerce types to JSON-safe primitives."""
    out: dict[str, Any] = {}
    for key in _SCHEMA_KEYS:
        if key not in settings:
            continue
        value = settings[key]
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
    return out


class UserConfigManager:
    """Read/write AI-Snake config with per-user and per-project files."""

    def __init__(self, *, cwd: Path | None = None) -> None:
        self._cwd = (cwd or Path.cwd()).resolve()
        self._global_path = global_config_path()
        self._project_path = project_config_path(self._cwd)
        self._cache: dict[str, Any] = {}
        self._dirty: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """Return merged settings: defaults → global → project."""
        merged: dict[str, Any] = dict(_DEFAULTS)
        merged.update(_read_json(self._global_path))
        merged.update(_read_json(self._project_path))
        self._cache = merged
        self._dirty = False
        return dict(merged)

    def save(self, settings: dict[str, Any]) -> bool:
        """Write settings immediately to the project file."""
        current = dict(self._cache) if self._cache else self.load()
        current.update({k: v for k, v in settings.items() if k in _SCHEMA_KEYS})
        self._cache = current
        self._dirty = False
        return _write_atomic(self._project_path, current)

    def save_from_game(self, game: dict[str, Any]) -> bool:
        """Extract all persistent keys from game state and write project file."""
        settings: dict[str, Any] = {}
        for key in _SCHEMA_KEYS:
            value = game.get(key)
            if isinstance(value, (str, int, float, bool)):
                settings[key] = value
        return self.save(settings)

    def flush(self, game: dict[str, Any]) -> tuple[bool, bool]:
        """Flush all config from game state to both project and global files.

        Returns (project_ok, global_ok).
        """
        settings: dict[str, Any] = {}
        for key in _SCHEMA_KEYS:
            value = game.get(key)
            if isinstance(value, (str, int, float, bool)):
                settings[key] = value
        project_ok = _write_atomic(self._project_path, settings)
        global_ok = _write_atomic(self._global_path, settings)
        self._dirty = False
        return project_ok, global_ok

    def apply_to_game(self, game: dict[str, Any]) -> dict[str, Any]:
        """Merge persisted settings into game dict. Returns updated game."""
        settings = self.load()
        game = dict(game)
        for key, value in settings.items():
            if key not in game or game[key] is None:
                game[key] = value
        return game

    def diagnostics(self) -> dict[str, Any]:
        return {
            "global_path": str(self._global_path),
            "project_path": str(self._project_path),
            "global_exists": self._global_path.exists(),
            "project_exists": self._project_path.exists(),
            "cache_keys": len(self._cache),
            "schema_version": SCHEMA_VERSION,
        }


# ── Module-level convenience ──────────────────────────────────────────────────

_manager: UserConfigManager | None = None


def get_manager(*, cwd: Path | None = None) -> UserConfigManager:
    global _manager
    if _manager is None:
        _manager = UserConfigManager(cwd=cwd)
    return _manager


def reset_manager() -> None:
    global _manager
    _manager = None


def load_user_config(*, cwd: Path | None = None) -> dict[str, Any]:
    return get_manager(cwd=cwd).load()


def save_user_config(settings: dict[str, Any], *, cwd: Path | None = None) -> bool:
    return get_manager(cwd=cwd).save(settings)


def flush_user_config(game: dict[str, Any], *, cwd: Path | None = None) -> tuple[bool, bool]:
    return get_manager(cwd=cwd).flush(game)
