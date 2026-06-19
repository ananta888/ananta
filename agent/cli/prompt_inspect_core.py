from __future__ import annotations

import json
import os
import sys
from typing import Any

_TRACE_SVC = None


def _get_trace_svc():
    global _TRACE_SVC
    if _TRACE_SVC is None:
        from agent.services.prompt_trace_service import get_prompt_trace_service
        _TRACE_SVC = get_prompt_trace_service()
    return _TRACE_SVC


def _reset_trace_svc_cache() -> None:
    """Clear the cached trace-service singleton.

    Intended for test teardown so that patches targeting
    ``agent.services.prompt_trace_service.get_prompt_trace_service``
    take effect on subsequent calls. Production CLI runs go through the
    process lifetime where the cache stays valid.
    """
    global _TRACE_SVC
    _TRACE_SVC = None

def _api_request(method: str, path: str, *, params: dict | None = None, timeout: int = 30):
    from agent.cli_goals import _request
    try:
        return _request(method, path, params=params, timeout=timeout)
    except SystemExit:
        return None

def _api_data(response) -> dict:
    from agent.cli_goals import _api_data
    if response is None:
        return {}
    data = _api_data(response)
    return data if isinstance(data, dict) else {}

def _load_llm_log_entries(limit: int = 2000) -> list[dict[str, Any]]:
    try:
        from agent.utils import get_data_dir
        log_path = os.path.join(get_data_dir(), "llm_log.jsonl")
        if not os.path.exists(log_path):
            return []
        rows: list[dict[str, Any]] = []
        with open(log_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        if limit > 0:
            rows = rows[-limit:]
        return rows
    except Exception:
        return []

def _latest_llm_response_by_request_id(limit: int = 2000) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for entry in _load_llm_log_entries(limit=limit):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("event") or "") != "llm_call_end":
            continue
        request_id = str(entry.get("request_id") or "").strip()
        if not request_id:
            continue
        current = latest.get(request_id)
        current_ts = float((current or {}).get("timestamp") or 0.0)
        entry_ts = float(entry.get("timestamp") or 0.0)
        if current is None or entry_ts >= current_ts:
            latest[request_id] = dict(entry)
    return latest

def _is_propose_like_request_kind(kind: str) -> bool:
    normalized = str(kind or "").strip().lower()
    return normalized in {
        "propose",
        "task_propose",
        "generate",
    }
