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
    "chat_include_task_memory",
    "chat_source_pack_id",
    "chat_retrieval_profile",
    "chat_retrieval_domain_hint",
    "chat_codecompass_trigger_mode",
    "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode",
    "chat_full_scan_source_only",
    "chat_full_scan_max_batches",
    "chat_full_scan_files_per_batch",
    "chat_full_scan_parallel_batches",
    "chat_full_scan_timeout_s",
    "chat_full_scan_chars_per_file",
    "chat_full_scan_max_input_tokens",
    # Context budgets
    "chat_context_chars",
    "chat_max_tokens",
    "chat_rag_top_k",
    "chat_answer_chars",
    "chat_answer_overflow_policy",
    "chat_never_truncate_answers",
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
    # Input history persistence
    "input_history_chat_enabled",
    "input_history_command_enabled",
    "input_history_max_entries",
    "chat_input_history",     # list[str]
    "command_input_history",  # list[str]
    # Chat sessions — each user has multiple named chat sessions with
    # their own settings. Persisted as a list of dicts, identical shape
    # to the runtime `ai_sessions` list in `chat_state`. Survives
    # snake restarts so the user doesn't lose their custom sessions.
    "chat_sessions",
    "chat_active_session_id",
    # Advanced chat configuration (env-mapped features)
    "chat_system_prompt",
    "chat_streaming",
    "chat_use_embedding_api",
    "chat_embedding_model",
    "chat_embedding_api_max_records",
})

_DEFAULTS: dict[str, Any] = {
    "chat_sessions": [],
    "chat_active_session_id": "code-help",
    "tutorial_mode": False,
    "ai_snake_provider_preference": "lmstudio",
    "ai_visual_use_codecompass": False,
    "chat_panel_open": True,
    "chat_backend": "ananta-worker",
    "chat_backend_model": "google/gemma-4-e4b",
    "chat_backend_api_base": "http://192.168.178.100:1234/v1",
    "chat_ask_timeout_s": 180.0,
    "chat_use_codecompass": True,
    "chat_include_local_project": True,
    "chat_include_wikipedia": False,
    "chat_include_task_memory": True,
    "chat_source_pack_id": "ananta-dev-default",
    "chat_retrieval_profile": "auto",
    "chat_retrieval_domain_hint": "",
    "chat_codecompass_trigger_mode": "auto",
    "chat_code_questions_repo_first": False,
    "chat_architecture_analysis_mode": "auto",
    "chat_full_scan_source_only": True,
    "chat_full_scan_max_batches": 8,
    "chat_full_scan_files_per_batch": 3,
    "chat_full_scan_parallel_batches": 1,
    "chat_full_scan_timeout_s": 1800,
    "chat_full_scan_chars_per_file": "600",
    "chat_full_scan_max_input_tokens": "auto",
    "chat_context_chars": 12000,
    "chat_max_tokens": 8000,
    "chat_rag_top_k": 120,
    "chat_answer_chars": 12000,
    "chat_answer_overflow_policy": "allow",
    "chat_never_truncate_answers": True,
    "chat_use_history": True,
    "chat_history_turns": 6,
    "chat_history_chars": 5000,
    "chat_use_summary": True,
    "chat_summary_chars": 4000,
    "chat_summary_update_every_turns": 3,
    "chat_pass_memory_to_worker": True,
    "chat_worker_mode": "snake_ask",
    "chat_backend_fallback": "lmstudio",
    "chat_include_runtime_status": False,
    # Input history persistence
    "input_history_chat_enabled": True,
    "input_history_command_enabled": True,
    "input_history_max_entries": 100,
    "chat_input_history": [],
    "command_input_history": [],
    # Advanced chat configuration
    "chat_system_prompt": "",
    "chat_streaming": True,
    "chat_use_embedding_api": False,
    "chat_embedding_model": "",
    "chat_embedding_api_max_records": 64,
}


def global_config_path() -> Path:
    return Path.home() / ".anana" / "user.json"


def project_config_path(cwd: Path | None = None) -> Path:
    base = (cwd or Path.cwd()).resolve()
    if (base / ".git").exists() and (base / "user.json").exists():
        return base / "data" / "user.json"
    return base / "user.json"


def project_seed_config_path(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()).resolve() / "user.json"


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


# Schema keys that store list[str] values
_LIST_SCHEMA_KEYS: frozenset[str] = frozenset({
    "chat_input_history",
    "command_input_history",
})


def _validated(settings: dict[str, Any]) -> dict[str, Any]:
    """Strip unknown keys and coerce types to JSON-safe primitives or list[str]."""
    out: dict[str, Any] = {}
    for key in _SCHEMA_KEYS:
        if key not in settings:
            continue
        value = settings[key]
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, list) and key in _LIST_SCHEMA_KEYS:
            # Allow list[str] only for designated history keys
            str_list = [str(item) for item in value if isinstance(item, (str, int, float))]
            out[key] = str_list
        elif key == "chat_sessions" and isinstance(value, list):
            # Sessions are list[dict]; already sanitized by _sanitize_sessions — pass through
            out[key] = value
    return out


def _extract_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Extract persistable settings, including list-backed input histories.

    This function is responsible for pulling specific keys/values out of
    a game dict and sanitizing them to their persistable, JSON-save
    representation. This includes flattening the runtime `chat_state`
    structure into two top-level keys (`chat_sessions`, `chat_active_session_id`)
    and cleaning various transient/sensitive data.
    """
    out: dict[str, Any] = {}
    # Extract all scalar + list[str] keys (e.g. chat_backend, chat_rag_top_k)
    # These come directly from the game dict top-level or are nested under
    # `chat` (if extracted from game["chat_state"]).
    # For persistence we flatten them.
    for key in _SCHEMA_KEYS:
        if key == "chat_sessions" or key == "chat_active_session_id": # Handled below
            continue

        value = settings.get(key)
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
        elif isinstance(value, list) and key in _LIST_SCHEMA_KEYS:
            out[key] = [str(item) for item in value if isinstance(item, (str, int, float)) and str(item).strip()]

    # Extract chat sessions separately because they're a nested list-of-dicts
    # in runtime, but a flat list in persistence.
    chat = settings.get("chat_state")
    if isinstance(chat, dict):
        sessions = chat.get("ai_sessions")
        if isinstance(sessions, list) and sessions:
            out["chat_sessions"] = _sanitize_sessions(sessions)
        active_id = chat.get("active_session_id")
        if isinstance(active_id, str) and active_id.strip():
            out["chat_active_session_id"] = active_id
        # Also extract chat history from chat_state if not already there
        if "chat_input_history" not in out and isinstance(chat.get("chat_input_history"), list):
            out["chat_input_history"] = [str(item) for item in chat["chat_input_history"] if str(item).strip()]
    elif "chat_sessions" in settings and isinstance(settings["chat_sessions"], list):
        # Already extracted flat sessions given directly (e.g., in save call)
        out["chat_sessions"] = _sanitize_sessions(settings["chat_sessions"])
        if "chat_active_session_id" in settings and isinstance(settings["chat_active_session_id"], str):
            out["chat_active_session_id"] = settings["chat_active_session_id"]
    return out


def _sanitize_sessions(sessions: list[Any]) -> list[dict[str, Any]]:
    """Strip a list of session dicts down to JSON-safe persisted shape.

    Kept fields: id, name, system_prompt, icon, settings (sanitized),
    created_at, updated_at. Dropped: messages, channels, anything else
    that the runtime chat pipeline owns.
    """
    out: list[dict[str, Any]] = []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if not sid:
            continue
        clean: dict[str, Any] = {"id": sid}
        for k in ("name", "system_prompt", "icon", "group"):
            v = item.get(k)
            if isinstance(v, str):
                clean[k] = v
            elif v is not None:
                clean[k] = str(v)
        for k in ("created_at", "updated_at"):
            v = item.get(k)
            if isinstance(v, (int, float)):
                clean[k] = v
        raw_settings = item.get("settings")
        if isinstance(raw_settings, dict):
            clean["settings"] = _sanitize_session_settings(raw_settings)
        raw_delta = item.get("settings_delta")
        if isinstance(raw_delta, dict):
            clean["settings_delta"] = _sanitize_session_settings(raw_delta)
        out.append(clean)
    return out


def _sanitize_session_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Sanitize a per-session settings dict to JSON-safe primitives."""
    out: dict[str, Any] = {}
    for k, v in settings.items():
        if isinstance(v, (bool, int, float, str)):
            out[k] = v
        # lists/dicts/None → silently dropped
    return out


class UserConfigManager:
    """Read/write AI-Snake config with per-user and per-project files."""

    def __init__(self, *, cwd: Path | None = None) -> None:
        self._cwd = (cwd or Path.cwd()).resolve()
        self._global_path = global_config_path()
        self._project_seed_path = project_seed_config_path(self._cwd)
        self._project_path = project_config_path(self._cwd)
        self._cache: dict[str, Any] = {}
        self._dirty: bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> dict[str, Any]:
        """Return merged settings: defaults → global → project."""
        merged: dict[str, Any] = dict(_DEFAULTS)
        merged.update(_read_json(self._global_path))
        project_seed_path = getattr(self, "_project_seed_path", self._project_path)
        if project_seed_path != self._project_path:
            merged.update(_read_json(project_seed_path))
        merged.update(_read_json(self._project_path))
        self._cache = merged
        self._dirty = False
        return dict(merged)

    def save(self, settings: dict[str, Any]) -> bool:
        """Write settings immediately to the project file."""
        current = dict(self._cache) if self._cache else self.load()
        current.update(_extract_settings(settings))
        self._cache = current
        self._dirty = False
        return _write_atomic(self._project_path, current)

    def save_from_game(self, game: dict[str, Any]) -> bool:
        """Extract all persistent keys from game state and write project file."""
        return self.save(_extract_settings(game))

    def flush(self, game: dict[str, Any]) -> tuple[bool, bool]:
        """Flush all config from game state to both project and global files.

        Returns (project_ok, global_ok).
        """
        settings = dict(self._cache) if self._cache else self.load()
        settings.update(_extract_settings(game))
        project_ok = _write_atomic(self._project_path, settings)
        global_ok = _write_atomic(self._global_path, settings)
        self._cache = settings
        self._dirty = False
        return project_ok, global_ok

    def apply_to_game(self, game: dict[str, Any]) -> dict[str, Any]:
        """Merge persisted settings into game dict. Returns updated game."""
        settings = self.load()
        game = dict(game)
        from client_surfaces.operator_tui.chat_state import get_chat_state, ensure_session_channels, default_sessions

        # Apply non-chat settings from the loaded config
        for key, value in settings.items():
            if key in {"chat_sessions", "chat_active_session_id"}:  # Handled below
                continue
            if key not in game or game[key] is None:
                # This is a safe merge; game values (that are already there)
                # override loaded settings to capture runtime state updates
                # from the TUI that are not yet persisted.
                game[key] = value

        # Initialize chat_state from game, ensuring defaults are present
        # for things like channels and internal state (e.g. chat_focus).
        chat = get_chat_state(game)

        # Overlay persisted sessions.
        # `settings` is the loaded user.json, so it has the canonical,
        # most up-to-date sessions. Merge those into the chat_state.
        persisted_sessions = settings.get("chat_sessions")
        if isinstance(persisted_sessions, list) and persisted_sessions:
            chat["ai_sessions"] = persisted_sessions
        else:
            # If no sessions are persisted, ensure default sessions are in chat_state
            chat["ai_sessions"] = default_sessions()

        # Restore active session ID
        persisted_active_id = settings.get("chat_active_session_id")
        if isinstance(persisted_active_id, str) and persisted_active_id.strip():
            chat["active_session_id"] = persisted_active_id
            chat["active_channel"] = f"ai:{persisted_active_id}"
        else:
             # Fallback to the first available session if none is persisted
            if chat["ai_sessions"]:
                chat["active_session_id"] = chat["ai_sessions"][0].get("id") or "code-help"
                chat["active_channel"] = "ai:" + str(chat["active_session_id"])
            else:
                chat["active_session_id"] = "code-help"
                chat["active_channel"] = "ai:code-help"

        ensure_session_channels(chat) # Re-create all session channels and apply display names

        # Crucially, update game with the now-merged chat_state
        game["chat_state"] = chat

        return game

    def diagnostics(self) -> dict[str, Any]:
        return {
            "global_path": str(self._global_path),
            "project_seed_path": str(getattr(self, "_project_seed_path", self._project_path)),
            "project_path": str(self._project_path),
            "global_exists": self._global_path.exists(),
            "project_seed_exists": getattr(self, "_project_seed_path", self._project_path).exists(),
            "project_exists": self._project_path.exists(),
            "cache_keys": len(self._cache),
            "schema_version": SCHEMA_VERSION,
        }


# ── Module-level convenience ──────────────────────────────────────────────────

_manager: UserConfigManager | None = None


def get_manager(*, cwd: Path | None = None) -> UserConfigManager:
    global _manager
    resolved_cwd = (cwd or Path.cwd()).resolve()
    if _manager is None or _manager._cwd != resolved_cwd:
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
