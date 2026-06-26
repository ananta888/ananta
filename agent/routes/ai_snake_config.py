"""T01 + T05: AI-Snake Config Read/Write + Options Endpoint."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from agent.auth import check_auth

ai_snake_config_bp = Blueprint("ai_snake_config", __name__)

_SCHEMA_KEYS: frozenset[str] = frozenset({
    "tutorial_mode", "ai_snake_provider_preference", "ai_visual_use_codecompass",
    "chat_panel_open", "chat_backend", "chat_backend_model", "chat_backend_api_base",
    "chat_ask_timeout_s", "chat_use_codecompass", "chat_include_local_project",
    "chat_include_wikipedia", "chat_source_pack_id", "chat_context_chars",
    "chat_max_tokens", "chat_rag_top_k", "chat_answer_chars",
    "chat_answer_overflow_policy", "chat_never_truncate_answers", "chat_use_history",
    "chat_history_turns", "chat_history_chars", "chat_use_summary",
    "chat_summary_chars", "chat_summary_update_every_turns",
    "chat_pass_memory_to_worker", "chat_worker_mode", "chat_backend_fallback",
    "chat_include_runtime_status", "input_history_chat_enabled",
    "input_history_command_enabled", "input_history_max_entries",
    # CRPS-007: retrieval profile config
    "chat_retrieval_profile", "chat_retrieval_domain_hint", "chat_code_questions_repo_first",
    "chat_architecture_analysis_mode", "chat_codecompass_trigger_mode",
    "chat_include_task_memory",
    # Retrieval strategy for CodeCompass semantic prefilter
    "chat_retrieval_strategy",
    # Embedding / Spracherkennung
    "embedding_model_id", "embedding_lang_detect",
    "embedding_lang_model_de", "embedding_lang_model_en",
    # Query-Reform / Intent-Extraktion
    "query_reform_mode", "query_reform_llm_backend", "query_reform_llm_model",
    # Full-scan chat budget
    "chat_full_scan_source_only", "chat_full_scan_max_batches", "chat_full_scan_files_per_batch",
    "chat_full_scan_parallel_batches", "chat_full_scan_timeout_s",
    "chat_full_scan_chars_per_file", "chat_full_scan_max_input_tokens",
    # RAG-Iterativ / Tool-Call Loop
    "rag_iterative_tool_calls_enabled",
    "rag_iterative_max_tool_calls", "rag_iterative_max_search_calls",
    "rag_iterative_import_depth", "rag_iterative_symbol_expand_max",
    "rag_iterative_catalog_chars", "rag_iterative_tool_chars_per_file",
    "rag_iterative_summarize_reads", "rag_iterative_summary_chars",
    "rag_iterative_initial_min_files", "rag_iterative_initial_max_files",
    # Trace/Tracking Viewer
    "ai_snake_trace_enabled", "ai_snake_trace_max_traces", "ai_snake_trace_max_events_per_trace",
    "ai_snake_trace_ttl_seconds", "ai_snake_trace_stream_mode", "ai_snake_trace_redact_secrets",
    "ai_snake_trace_max_preview_chars",
})

_DEFAULTS: dict[str, Any] = {
    "tutorial_mode": False,
    "ai_snake_provider_preference": "lmstudio",
    "ai_visual_use_codecompass": False,
    "chat_panel_open": True,
    "chat_backend": "ananta-worker",
    "chat_backend_model": "",
    "chat_backend_api_base": "http://localhost:1234/v1",
    "chat_ask_timeout_s": 180,
    "chat_use_codecompass": True,
    "chat_include_local_project": True,
    "chat_include_wikipedia": False,
    "chat_source_pack_id": "ananta-dev-default",
    "chat_context_chars": 12000,
    "chat_max_tokens": 8000,
    "chat_rag_top_k": 120,
    "chat_answer_chars": 12000,
    "chat_answer_overflow_policy": "allow",
    "chat_never_truncate_answers": True,
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
    "input_history_chat_enabled": True,
    "input_history_command_enabled": True,
    "input_history_max_entries": 100,
    # CRPS-007: retrieval profile config
    "chat_retrieval_profile": "auto",
    "chat_retrieval_domain_hint": "",
    "chat_codecompass_trigger_mode": "auto",
    "chat_code_questions_repo_first": False,
    "chat_retrieval_strategy": "semantic_prefilter",
    # Embedding / Spracherkennung
    "embedding_model_id": "paraphrase-multilingual-MiniLM-L12-v2",
    "embedding_lang_detect": False,
    "embedding_lang_model_de": "paraphrase-multilingual-MiniLM-L12-v2",
    "embedding_lang_model_en": "all-MiniLM-L6-v2",
    # Query-Reform / Intent-Extraktion
    "query_reform_mode": "off",
    "query_reform_llm_backend": "ananta-worker",
    "query_reform_llm_model": "",
    "chat_include_task_memory": True,
    "chat_architecture_analysis_mode": "auto",
    "chat_full_scan_source_only": True,
    "chat_full_scan_max_batches": 8,
    "chat_full_scan_files_per_batch": 3,
    "chat_full_scan_parallel_batches": 1,
    "chat_full_scan_timeout_s": 1800,
    "chat_full_scan_chars_per_file": 600,
    "chat_full_scan_max_input_tokens": "auto",
    # RAG-Iterativ / Tool-Call Loop
    "rag_iterative_tool_calls_enabled": True,
    "rag_iterative_max_tool_calls": 0,
    "rag_iterative_max_search_calls": 0,
    "rag_iterative_import_depth": 0,
    "rag_iterative_symbol_expand_max": 0,
    "rag_iterative_catalog_chars": 20000,
    "rag_iterative_tool_chars_per_file": 20000,
    "rag_iterative_summarize_reads": True,
    "rag_iterative_summary_chars": 600,
    "rag_iterative_initial_min_files": 3,
    "rag_iterative_initial_max_files": 8,
    # Trace/Tracking Viewer
    "ai_snake_trace_enabled": True,
    "ai_snake_trace_max_traces": 50,
    "ai_snake_trace_max_events_per_trace": 500,
    "ai_snake_trace_ttl_seconds": 86400,
    "ai_snake_trace_stream_mode": "polling",
    "ai_snake_trace_redact_secrets": True,
    "ai_snake_trace_max_preview_chars": 200000,
}

_OPTIONS: dict[str, list[str]] = {
    "ai_snake_provider_preference": ["lmstudio", "opencode", "hermes", "worker-propose"],
    "chat_backend": ["ananta-worker", "opencode", "lmstudio", "hermes"],
    "chat_backend_api_base": [
        "http://localhost:1234/v1", "http://localhost:8080/v1",
        "http://localhost:11434/v1", "http://127.0.0.1:1234/v1",
    ],
    "chat_ask_timeout_s": ["20", "30", "45", "60", "90", "120", "180", "300", "600", "1200", "1800"],
    "chat_source_pack_id": ["ananta-dev-default", "ananta-default", "ananta-local-only"],
    "chat_context_chars": ["1000", "2000", "3000", "5000", "8000", "12000"],
    "chat_max_tokens": ["400", "800", "1200", "2000", "4000", "8000"],
    "chat_rag_top_k": ["12", "24", "32", "48", "64", "96", "120"],
    "chat_answer_chars": ["600", "1200", "2400", "4000", "6000", "8000", "12000", "16000", "24000"],
    "chat_answer_overflow_policy": ["allow", "summarize", "truncate"],
    "chat_history_turns": ["3", "6", "10", "15", "20", "30"],
    "chat_history_chars": ["600", "1200", "1800", "3000", "5000"],
    "chat_summary_chars": ["500", "1000", "1500", "2500", "4000"],
    "chat_summary_update_every_turns": ["1", "2", "3", "5", "10"],
    "chat_worker_mode": ["snake_ask", "propose", "auto"],
    "chat_backend_fallback": ["none", "lmstudio", "local_knowledge"],
    "input_history_max_entries": ["20", "50", "100", "200", "500"],
    "chat_retrieval_profile": ["auto", "repo_first", "docs_first", "legacy"],
    "chat_codecompass_trigger_mode": ["auto", "force_codecompass", "force_repo_first", "disabled"],
    "chat_retrieval_strategy": ["semantic_prefilter", "direct", "transformer_rerank", "hybrid"],
    "embedding_model_id": [
        "paraphrase-multilingual-MiniLM-L12-v2",
        "all-MiniLM-L6-v2",
        "deepset/gbert-base",
        "intfloat/multilingual-e5-small",
    ],
    "embedding_lang_model_de": [
        "paraphrase-multilingual-MiniLM-L12-v2",
        "deepset/gbert-base",
        "intfloat/multilingual-e5-small",
    ],
    "embedding_lang_model_en": [
        "all-MiniLM-L6-v2",
        "paraphrase-multilingual-MiniLM-L12-v2",
        "intfloat/multilingual-e5-small",
    ],
    "query_reform_mode": ["off", "regex", "regex_embed", "llm"],
    "query_reform_llm_backend": ["ananta-worker", "opencode", "lmstudio", "hermes"],
    "chat_architecture_analysis_mode": ["auto", "rag_iterative", "standard", "full_scan", "off"],
    "chat_full_scan_max_batches": ["2", "4", "6", "8", "12", "16"],
    "chat_full_scan_files_per_batch": ["1", "2", "3", "5", "8"],
    "chat_full_scan_parallel_batches": ["1", "2", "3", "4", "6", "8"],
    "chat_full_scan_timeout_s": ["300", "600", "900", "1200", "1800", "3600"],
    "chat_full_scan_chars_per_file": ["300", "600", "1200", "2000", "3500"],
    "chat_full_scan_max_input_tokens": ["auto", "1500", "3000", "6000", "12000", "24000"],
    "rag_iterative_initial_min_files": ["0", "1", "2", "3", "4", "5"],
    "rag_iterative_initial_max_files": ["3", "5", "8", "10", "12", "16"],
}

_BOOL_KEYS = frozenset(k for k, v in _DEFAULTS.items() if isinstance(v, bool))


def _user_json_path() -> Path:
    return Path(os.environ.get("ANANTA_USER_JSON", "user.json")).resolve()


def _load_raw() -> dict[str, Any]:
    """Read the raw file content without schema interpretation."""
    p = _user_json_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _load() -> dict[str, Any]:
    """Read settings, supporting TUI format ({settings: {...}}) and legacy flat format."""
    raw = _load_raw()
    nested = raw.get("settings")
    if isinstance(nested, dict):
        return nested
    return raw


def _save(data: dict[str, Any], *, clear_session_overrides: bool = False) -> None:
    p = _user_json_path()
    tmp = p.with_suffix(".json.tmp")
    try:
        raw = _load_raw()
        # Write into the nested "settings" key if TUI format is present, otherwise flat
        if isinstance(raw.get("settings"), dict):
            raw["settings"].update(data)
            settings_dict = raw["settings"]
        else:
            raw.update(data)
            settings_dict = raw
        # When patching the base config, remove the same keys from the active session's
        # settings_delta so GET reflects the patched value rather than the stale override.
        if clear_session_overrides:
            active_id = str((settings_dict.get("chat_active_session_id") or "")).strip()
            if active_id:
                for session in (settings_dict.get("chat_sessions") or []):
                    if str(session.get("id") or "") == active_id:
                        delta = session.get("settings_delta")
                        if isinstance(delta, dict):
                            for k in data:
                                delta.pop(k, None)
                        break
        raw["_updated_at"] = time.time()
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise


def _current_config() -> dict[str, Any]:
    stored = _load()
    base = {k: stored.get(k, _DEFAULTS.get(k)) for k in _SCHEMA_KEYS}
    active_id = str(stored.get("chat_active_session_id") or "").strip()
    if active_id:
        for session in (stored.get("chat_sessions") or []):
            if str(session.get("id") or "") == active_id:
                for k, v in (session.get("settings_delta") or {}).items():
                    if k in _SCHEMA_KEYS:
                        base[k] = v
                break
    return base


@ai_snake_config_bp.route("/ai-snake/config", methods=["GET"])
@check_auth
def get_ai_snake_config():
    return jsonify({"ok": True, "config": _current_config()})


@ai_snake_config_bp.route("/ai-snake/config", methods=["PATCH"])
@check_auth
def patch_ai_snake_config():
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "expected JSON object"}), 400
    updates: dict[str, Any] = {}
    rejected = []
    for key, raw_value in body.items():
        if key not in _SCHEMA_KEYS:
            rejected.append(key)
            continue
        if key in _BOOL_KEYS:
            updates[key] = bool(raw_value)
        elif isinstance(_DEFAULTS.get(key), (int, float)) and not isinstance(_DEFAULTS.get(key), bool):
            try:
                updates[key] = type(_DEFAULTS[key])(raw_value)
            except (TypeError, ValueError):
                rejected.append(key)
        else:
            updates[key] = str(raw_value) if raw_value is not None else ""
    if not updates and rejected:
        return jsonify({"ok": False, "error": "no valid keys", "rejected": rejected}), 422
    _save(updates, clear_session_overrides=True)
    return jsonify({"ok": True, "saved": list(updates.keys()), "rejected": rejected})


@ai_snake_config_bp.route("/ai-snake/config/options", methods=["GET"])
@check_auth
def get_ai_snake_config_options():
    return jsonify({"ok": True, "options": _OPTIONS, "defaults": _DEFAULTS, "bool_keys": list(_BOOL_KEYS)})
