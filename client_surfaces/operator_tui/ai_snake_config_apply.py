"""Applying an edited AI-Snake config value to the game state.

``ai_snake_config_view.apply_ai_snake_config_value`` resolves the config row
and delegates here. The per-key rules are declarative tables instead of one
long ``if key == ...`` chain:

* :data:`BOOL_FIELD_BY_KEY` - ``bool`` rows and the game field they set
* :data:`VALUE_HANDLERS` - value rows that are checked before the generic
  free-text rule (clamped integers, closed choices and special cases)
* :data:`POST_TEXT_VALUE_HANDLERS` - rows checked only after the free-text
  rule, exactly as in the former chain

Every handler is ``handler(game, key, label, raw_value) -> status`` and
persists the chat settings itself when it changed the game. Adding a config
key is one table entry (OCP); each rule is one small function or value object
(SRP).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from client_surfaces.operator_tui._ai_snake_config_helpers import _persist_tui_chat_settings

Game = dict[str, object]
ValueHandler = Callable[[Game, str, str, str], str]


class ModelRefresher(Protocol):
    def __call__(self, game: Game, *, force: bool = False) -> tuple[list[str], str]: ...


def parse_bool_value(raw: str) -> bool | None:
    token = str(raw or "").strip().lower()
    if token in {"1", "true", "t", "yes", "y", "on", "an", "ja"}:
        return True
    if token in {"0", "false", "f", "no", "n", "off", "aus", "nein"}:
        return False
    return None


def _on_off(parsed: bool) -> str:
    return "AN" if parsed else "AUS"


# --- bool rows ------------------------------------------------------------------------

BOOL_FIELD_BY_KEY: dict[str, str] = {
    "visual_enabled": "tutorial_mode",
    "chat_panel_open": "chat_panel_open",
    "visual_codecompass": "ai_visual_use_codecompass",
    "chat_use_codecompass": "chat_use_codecompass",
    "chat_include_local_project": "chat_include_local_project",
    "chat_include_wikipedia": "chat_include_wikipedia",
    "chat_include_task_memory": "chat_include_task_memory",
    "chat_use_history": "chat_use_history",
    "chat_use_summary": "chat_use_summary",
    "chat_pass_memory_to_worker": "chat_pass_memory_to_worker",
    "chat_include_runtime_status": "chat_include_runtime_status",
    "chat_code_questions_repo_first": "chat_code_questions_repo_first",
    "chat_never_truncate_answers": "chat_never_truncate_answers",
}


def apply_bool_row(game: Game, key: str, label: str, raw_value: str) -> str:
    """A ``bool`` row: known keys set their game field and persist; others only echo."""
    parsed = parse_bool_value(raw_value)
    if parsed is None:
        return f"ai config: {label} erwartet AN/AUS"
    field = BOOL_FIELD_BY_KEY.get(key)
    if field is not None:
        game[field] = parsed
        _persist_tui_chat_settings(game)
    return f"ai config: {label} {_on_off(parsed)}"


# --- reusable value rules -------------------------------------------------------------


@dataclass(frozen=True)
class StoreText:
    """Store the raw text in ``field`` (default: the config key)."""

    field: str = ""

    def __call__(self, game: Game, key: str, label: str, raw_value: str) -> str:
        game[self.field or key] = raw_value
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {raw_value}"


@dataclass(frozen=True)
class ClampedInt:
    """An integer clamped to ``[low, high]``."""

    low: int
    high: int

    def __call__(self, game: Game, key: str, label: str, raw_value: str) -> str:
        try:
            value_int = int(raw_value)
        except ValueError:
            return f"ai config: {label} erwartet zahl"
        game[key] = max(self.low, min(self.high, value_int))
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {game[key]}"


@dataclass(frozen=True)
class Choice:
    """One value of a closed set; ``expected`` overrides the ``a/b/c`` error hint."""

    allowed: tuple[str, ...]
    expected: str = ""
    empty_display: str = ""

    def __call__(self, game: Game, key: str, label: str, raw_value: str) -> str:
        if raw_value not in self.allowed:
            return f"ai config: {label} erwartet {self.expected or '/'.join(self.allowed)}"
        game[key] = raw_value
        _persist_tui_chat_settings(game)
        return f"ai config: {label} -> {raw_value or self.empty_display}"


@dataclass(frozen=True)
class BoolField:
    """A bool parsed with :func:`parse_bool_value` on a non-``bool`` row."""

    def __call__(self, game: Game, key: str, label: str, raw_value: str) -> str:
        parsed = parse_bool_value(raw_value)
        if parsed is None:
            return f"ai config: {label} erwartet AN/AUS"
        game[key] = parsed
        _persist_tui_chat_settings(game)
        return f"ai config: {label} {_on_off(parsed)}"


# --- special cases ---------------------------------------------------------------------


def _apply_chat_model(game: Game, key: str, label: str, raw_value: str) -> str:
    game["chat_backend_model"] = raw_value
    models_raw = game.get("chat_backend_models")
    models = [str(item).strip() for item in models_raw] if isinstance(models_raw, list) else []
    if raw_value not in models:
        models.append(raw_value)
    game["chat_backend_models"] = [m for m in models if m][-40:]
    _persist_tui_chat_settings(game)
    return f"ai config: {label} -> {raw_value}"


def _apply_chat_api_base(game: Game, key: str, label: str, raw_value: str) -> str:
    game["chat_backend_api_base"] = raw_value.rstrip("/")
    game["chat_backend_models_last_refresh_at"] = 0.0
    _persist_tui_chat_settings(game)
    return f"ai config: {label} -> {game['chat_backend_api_base']}"


def _apply_chat_ask_timeout(game: Game, key: str, label: str, raw_value: str) -> str:
    try:
        timeout_s = float(raw_value)
    except ValueError:
        return f"ai config: {label} erwartet sekunden"
    timeout_s = max(3.0, min(1800.0, timeout_s))
    game["chat_ask_timeout_s"] = timeout_s
    _persist_tui_chat_settings(game)
    return f"ai config: {label} -> {timeout_s:g}s"


def _apply_full_scan_source_only(game: Game, key: str, label: str, raw_value: str) -> str:
    bool_val = str(raw_value).lower() not in {"false", "0", "nein", "no", "off"}
    game[key] = bool_val
    _persist_tui_chat_settings(game)
    return f"ai config: {label} -> {bool_val}"


def _apply_full_scan_max_input_tokens(game: Game, key: str, label: str, raw_value: str) -> str:
    token = str(raw_value or "").strip().lower()
    if token in {"", "auto"}:
        game.pop("chat_full_scan_max_input_tokens", None)
    else:
        try:
            value_int = int(token)
        except ValueError:
            return f"ai config: {label} erwartet zahl oder 'auto'"
        game[key] = max(256, min(200000, value_int))
    _persist_tui_chat_settings(game)
    return f"ai config: {label} -> {game.get(key, 'auto')}"


def apply_free_text(game: Game, key: str, label: str, raw_value: str) -> str:
    """A free-form ``text`` row: store the raw value (it may contain spaces)."""
    game[key] = raw_value
    _persist_tui_chat_settings(game)
    if not raw_value:
        return f"ai config: {label} zurueckgesetzt (default)"
    preview = raw_value if len(raw_value) <= 60 else raw_value[:57] + "…"
    return f"ai config: {label} -> {preview}"


def chat_backend_handler(refresh_models: ModelRefresher) -> ValueHandler:
    """Switching the chat backend refreshes its model list through ``refresh_models``."""

    def apply(game: Game, key: str, label: str, raw_value: str) -> str:
        game["chat_backend"] = raw_value
        game["chat_backend_models_last_refresh_at"] = 0.0
        models, fetch_error = refresh_models(game, force=True)
        current_model = str(game.get("chat_backend_model") or "").strip()
        if models and (not current_model or current_model == "-"):
            game["chat_backend_model"] = models[0]
        _persist_tui_chat_settings(game)
        if models:
            return f"ai config: {label} -> {raw_value} ({len(models)} modelle)"
        if fetch_error:
            return f"ai config: {label} -> {raw_value} ({fetch_error})"
        return f"ai config: {label} -> {raw_value}"

    return apply


_RETRIEVAL_DOMAINS = ("", "codecompass", "ai_snake", "worker", "ananta_game", "operator_tui", "ops", "generic")

VALUE_HANDLERS: dict[str, ValueHandler] = {
    "visual_provider": StoreText("ai_snake_provider_preference"),
    "chat_model": _apply_chat_model,
    "chat_api_base": _apply_chat_api_base,
    "chat_ask_timeout_s": _apply_chat_ask_timeout,
    "chat_source_pack_id": StoreText(),
    "chat_context_chars": ClampedInt(500, 20000),
    "chat_max_tokens": ClampedInt(100, 8000),
    "chat_rag_top_k": ClampedInt(8, 120),
    "chat_answer_chars": ClampedInt(600, 50000),
    "chat_answer_overflow_policy": Choice(("allow", "summarize", "truncate")),
    "chat_retrieval_profile": Choice(("auto", "repo_first", "docs_first", "legacy")),
    # Valid values mirror the backend resolve_profile enum.
    "chat_codecompass_trigger_mode": Choice(("auto", "force_codecompass", "force_repo_first", "disabled")),
    # Choice values: empty (auto) + the known DOMAIN_* constants.
    "chat_retrieval_domain_hint": Choice(_RETRIEVAL_DOMAINS, expected="eine Domain oder leer", empty_display="auto"),
    "chat_architecture_analysis_mode": Choice(("auto", "rag_iterative", "standard", "full_scan", "off")),
    "chat_code_question_max_tool_calls": ClampedInt(0, 100),
    "chat_code_question_max_search_calls": ClampedInt(0, 100),
    "chat_full_scan_source_only": _apply_full_scan_source_only,
    "chat_full_scan_max_batches": ClampedInt(1, 16),
    "chat_full_scan_files_per_batch": ClampedInt(1, 10),
    "chat_full_scan_parallel_batches": ClampedInt(1, 8),
    "chat_full_scan_timeout_s": ClampedInt(60, 7200),
    "chat_full_scan_chars_per_file": ClampedInt(100, 20000),
    "chat_full_scan_max_input_tokens": _apply_full_scan_max_input_tokens,
    "chat_history_turns": ClampedInt(1, 30),
    "chat_history_chars": ClampedInt(100, 10000),
    "chat_summary_chars": ClampedInt(100, 5000),
    "chat_summary_update_every_turns": ClampedInt(1, 20),
    "chat_worker_mode": Choice(("snake_ask", "propose", "auto")),
    "chat_backend_fallback": Choice(("none", "lmstudio", "local_knowledge")),
}

POST_TEXT_VALUE_HANDLERS: dict[str, ValueHandler] = {
    "chat_streaming": BoolField(),
    "chat_use_embedding_api": BoolField(),
    "chat_embedding_api_max_records": ClampedInt(1, 128),
}


def apply_config_row_value(
    game: Game,
    row: dict[str, object],
    *,
    key: str,
    value: str,
    refresh_models: ModelRefresher,
) -> str:
    """Apply ``value`` for the resolved config ``row`` and return the status line."""
    label = str(row.get("label") or key)
    row_type = str(row.get("type") or "")
    raw_value = str(value or "").strip()
    if not raw_value:
        return f"ai config: {label} leer"
    if row_type == "bool":
        return apply_bool_row(game, key, label, raw_value)
    if key == "chat_backend":
        return chat_backend_handler(refresh_models)(game, key, label, raw_value)
    handler = VALUE_HANDLERS.get(key)
    if handler is None and row_type == "text":
        handler = apply_free_text
    if handler is None:
        handler = POST_TEXT_VALUE_HANDLERS.get(key)
    if handler is None:
        return "ai config: keine änderung"
    return handler(game, key, label, raw_value)
