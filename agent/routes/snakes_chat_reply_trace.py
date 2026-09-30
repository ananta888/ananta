"""Trace/summary projections of the AI-Snake chat reply pipeline.

Pure functions that build the trace event details and the bracketed room
summaries of :class:`agent.routes.snakes_chat_reply_runner.SnakeChatReplyRunner`.
"""

from __future__ import annotations

from typing import Any

from .snakes_worker_routing import snake_profile_routing_enabled

_MAX_LISTED_FILES = 6


def config_loaded_details(
    *,
    provider: str,
    model: str | None,
    backend: Any,
    session_id: str,
    history_messages: int,
) -> dict[str, Any]:
    """Details of the ``config_loaded`` trace event (requested vs. effective routing)."""

    profile_routing = snake_profile_routing_enabled()
    return {
        "requested_provider": provider,
        "requested_model": model,
        "session_requested_provider": provider,
        "session_requested_model": model,
        "backend": backend,
        "effective_runtime": "hub_worker_profile_routing" if profile_routing else "legacy_direct_provider",
        "routing_authority": "local_model_profile" if profile_routing else "session_provider_config",
        "session_metadata_advisory": profile_routing,
        "effective_provider": "hub_worker_profile" if profile_routing else provider,
        "effective_model": "resolved_per_task" if profile_routing else model,
        "expected_research_profile": "local_lfm25_agentic_fast" if profile_routing else None,
        "expected_synthesis_profile": "local_kat_coder_v25_heavy" if profile_routing else None,
        "session_id": session_id,
        "conversation_history_messages": history_messages,
    }


def llm_call_started_summary(provider: str, model: str | None, prompt_chars: int, profile_routing: bool) -> str:
    target = "lokales Hub-Worker-Profil — " if profile_routing else f"{provider} / {model or 'default'} — "
    return target + f"{prompt_chars} Zeichen Eingabe"


def _file_names(paths: list) -> str:
    names = ", ".join(str(path).split("/")[-1] for path in paths[:_MAX_LISTED_FILES])
    if len(paths) > _MAX_LISTED_FILES:
        names += f" +{len(paths) - _MAX_LISTED_FILES}"
    return names


def rag_iterative_summary(scan_trace: dict[str, Any]) -> str:
    """Room summary of a bounded agentic RAG / iterative batch run."""

    tool_loop = scan_trace.get("tool_loop") or {}
    if scan_trace.get("cancelled") or tool_loop.get("cancelled"):
        return "rag_iterative: abgebrochen"
    if tool_loop or scan_trace.get("available_files"):
        available = scan_trace.get("available_files") or []
        names = _file_names(available)
        summary = f"rag_iterative: {tool_loop.get('tool_calls_made', 0)} Tool-Calls, {len(available)} Dateien verfügbar"
        return summary + (f" ({names})" if names else "")
    names = _file_names(scan_trace.get("file_list") or [])
    summary = (
        f"rag_iterative: {scan_trace.get('batches_completed', 0)} Batches, "
        f"{scan_trace.get('files_resolved', 0)} Dateien"
    )
    return summary + (f" ({names})" if names else "")


def rag_iterative_trace_status(*, cancelled: bool, synthesis_degraded: bool, answered: bool) -> str:
    if cancelled:
        return "cancelled"
    if synthesis_degraded:
        return "warning"
    return "completed" if answered else "failed"
