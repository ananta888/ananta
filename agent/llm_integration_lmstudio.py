import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from agent.config import settings
from agent.utils import _http_get, get_data_dir, read_json, update_json, write_json

_LMSTUDIO_HISTORY_FILE = "llm_model_history.json"


def _sha256_text(value: str | None) -> str | None:
    text = str(value or "")
    if not text:
        return None
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _model_identifier_tokens(value: str | None) -> set[str]:
    normalized = re.sub(r"[^a-z0-9.]+", " ", str(value or "").strip().lower())
    return {token for token in normalized.split() if token}


def _model_identifier_matches(left: str | None, right: str | None) -> bool:
    left_value = str(left or "").strip()
    right_value = str(right or "").strip()
    if not left_value or not right_value:
        return False
    if left_value.lower() == right_value.lower():
        return True
    left_tokens = _model_identifier_tokens(left_value)
    right_tokens = _model_identifier_tokens(right_value)
    overlap = left_tokens & right_tokens
    if len(overlap) < 2:
        return False
    return left_tokens.issubset(right_tokens) or right_tokens.issubset(left_tokens)


def _find_matching_lmstudio_candidate(model: str | None, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_model = str(model or "").strip()
    if not normalized_model:
        return None
    for candidate in candidates:
        candidate_id = str((candidate or {}).get("id") or "").strip()
        if _model_identifier_matches(normalized_model, candidate_id):
            return candidate
    return None


def _load_lmstudio_history() -> dict:
    data_dir = get_data_dir()
    path = os.path.join(data_dir, _LMSTUDIO_HISTORY_FILE)
    return read_json(path, {"models": {}})


def _save_lmstudio_history(history: dict) -> None:
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, _LMSTUDIO_HISTORY_FILE)
    write_json(path, history)


def _touch_lmstudio_models(history: dict, model_ids: list[str]) -> dict:
    models = history.setdefault("models", {})
    now = int(time.time())
    for mid in model_ids:
        if mid not in models:
            models[mid] = {
                "success": 0,
                "fail": 0,
                "last_success": None,
                "last_fail": None,
                "last_used": None,
                "first_seen": now,
            }
    return history


def _record_lmstudio_result(history: dict, model_id: str, success: bool) -> dict:
    if not model_id:
        return history
    models = history.setdefault("models", {})
    entry = models.setdefault(
        model_id,
        {
            "success": 0,
            "fail": 0,
            "last_success": None,
            "last_fail": None,
            "last_used": None,
            "first_seen": int(time.time()),
        },
    )
    now = int(time.time())
    entry["last_used"] = now
    if success:
        entry["success"] = int(entry.get("success", 0)) + 1
        entry["last_success"] = now
    else:
        entry["fail"] = int(entry.get("fail", 0)) + 1
        entry["last_fail"] = now
    models[model_id] = entry
    history["models"] = models
    return history


def _update_lmstudio_history(model_id: str, success: bool) -> None:
    if not model_id:
        return
    data_dir = get_data_dir()
    path = os.path.join(data_dir, _LMSTUDIO_HISTORY_FILE)

    def _update(data: dict) -> dict:
        if not isinstance(data, dict):
            data = {"models": {}}
        return _record_lmstudio_result(data, model_id, success)

    update_json(path, _update, default={"models": {}})


def _prepare_lmstudio_history(candidates: list[dict]) -> dict:
    history = _load_lmstudio_history()
    history = _touch_lmstudio_models(history, [c.get("id") for c in candidates if c.get("id")])
    _save_lmstudio_history(history)
    return history


def _select_best_lmstudio_model(candidates: list[dict], history: dict) -> dict | None:
    if not candidates:
        return None

    min_ctx = getattr(settings, "lmstudio_max_context_tokens", 0)
    api_mode = getattr(settings, "lmstudio_api_mode", "chat")

    filtered = [c for c in candidates if (c.get("context_length") or 0) >= min_ctx]
    if not filtered:
        filtered = list(candidates)

    if api_mode == "chat":
        chat_filtered = [
            c for c in filtered if "chat" in (c.get("id") or "").lower() or "instruct" in (c.get("id") or "").lower()
        ]
        if chat_filtered:
            filtered = chat_filtered
        elif not filtered:
            filtered = list(candidates)

    filtered = sorted(filtered, key=lambda x: x.get("id") or "")

    models_hist = history.get("models", {})

    def _score(item: dict) -> tuple:
        mid = item.get("id") or ""
        h = models_hist.get(mid, {})
        success = int(h.get("success", 0))
        fail = int(h.get("fail", 0))
        total = success + fail
        success_rate = (success / total) if total > 0 else -1.0
        last_success = h.get("last_success") or 0
        last_used = h.get("last_used") or 0
        return (1 if success > 0 else 0, success_rate, success, last_success, last_used)

    if any(int(models_hist.get(c.get("id") or "", {}).get("success", 0)) > 0 for c in filtered):
        return sorted(filtered, key=_score, reverse=True)[0]

    for c in filtered:
        mid = c.get("id") or ""
        h = models_hist.get(mid)
        if not h or (int(h.get("success", 0)) + int(h.get("fail", 0)) == 0):
            return c

    def _fallback_score(item: dict) -> tuple:
        mid = item.get("id") or ""
        h = models_hist.get(mid, {})
        fail = int(h.get("fail", 0))
        last_used = h.get("last_used") or 0
        return (fail, -last_used)

    if not filtered:
        return sorted(candidates, key=lambda x: x.get("id") or "")[0]
    return sorted(filtered, key=_fallback_score)[0]


def _normalize_lmstudio_base_url(base_url: str | None) -> Optional[str]:
    raw_url = str(base_url or "").strip()
    if not raw_url:
        return None

    normalized = raw_url.rstrip("/")
    normalized_lower = normalized.lower()
    for suffix in ("/chat/completions", "/completions", "/responses", "/models"):
        if normalized_lower.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break

    parsed = urlsplit(normalized)
    if not parsed.scheme or not parsed.netloc:
        return None

    path = parsed.path.rstrip("/")
    path_lower = path.lower()
    if path_lower.endswith("/v1"):
        resolved_path = path
    elif "/v1" in path_lower:
        idx = path_lower.index("/v1")
        resolved_path = path[: idx + 3]
    elif not path:
        resolved_path = "/v1"
    else:
        resolved_path = f"{path}/v1"

    return urlunsplit((parsed.scheme, parsed.netloc, resolved_path, "", ""))


def _lmstudio_models_url(base_url: str) -> Optional[str]:
    normalized = _normalize_lmstudio_base_url(base_url)
    if not normalized:
        return None
    return f"{normalized}/models"


def _resolve_lmstudio_model(model: Optional[str], base_url: str, timeout: int) -> Optional[dict]:
    candidates = _list_lmstudio_candidates(base_url, timeout)
    if model and str(model).strip().lower() != "auto":
        matched = _find_matching_lmstudio_candidate(model, candidates)
        return matched or {"id": model}
    if candidates:
        history = _prepare_lmstudio_history(candidates)
        if not model or str(model).strip().lower() == "auto":
            best = _select_best_lmstudio_model(candidates, history)
            if best:
                return best
        return candidates[0]
    return None


def _extract_lmstudio_candidates(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return []

    llm_candidates = []
    for item in data:
        if not isinstance(item, dict):
            continue
        mid = item.get("id") or item.get("name") or ""
        if "embed" in str(mid).lower():
            continue
        llm_candidates.append(
            {
                "id": mid,
                "context_length": item.get("context_length") or item.get("max_context_length") or item.get("n_ctx"),
            }
        )

    if llm_candidates:
        return llm_candidates

    first = data[0]
    if isinstance(first, dict):
        return [
            {
                "id": first.get("id") or first.get("name"),
                "context_length": first.get("context_length") or first.get("max_context_length") or first.get("n_ctx"),
            }
        ]
    return []


def _list_lmstudio_candidates(base_url: str, timeout: int) -> list[dict]:
    models_url = _lmstudio_models_url(base_url)
    if not models_url:
        return []
    try:
        resp = _http_get(models_url, timeout=timeout, silent=True)
    except Exception:
        return []

    return _extract_lmstudio_candidates(resp)


def probe_lmstudio_runtime(base_url: str, timeout: int) -> dict[str, Any]:
    models_url = _lmstudio_models_url(base_url)
    if not models_url:
        return {
            "ok": False,
            "status": "invalid_url",
            "base_url": base_url,
            "models_url": None,
            "candidates": [],
            "candidate_count": 0,
        }
    try:
        resp = _http_get(models_url, timeout=timeout, silent=True)
    except Exception:
        return {
            "ok": False,
            "status": "error",
            "base_url": base_url,
            "models_url": models_url,
            "candidates": [],
            "candidate_count": 0,
        }

    candidates = _extract_lmstudio_candidates(resp)
    status = "ok" if candidates else "reachable_no_models"
    return {
        "ok": True,
        "status": status,
        "base_url": base_url,
        "models_url": models_url,
        "candidates": candidates,
        "candidate_count": len(candidates),
    }


def _extract_lmstudio_text(payload: Any) -> str:
    if not payload:
        return ""
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return ""
    if "response" in payload:
        return str(payload.get("response") or "")

    choices = payload.get("choices")
    if not choices or not isinstance(choices, list):
        error = payload.get("error")
        if isinstance(error, dict):
            return f"Error: {error.get('message', 'Unknown LMStudio error')}"
        if error is not None:
            return f"Error: {error}"
        return ""

    first = choices[0] if choices else None
    if not isinstance(first, dict):
        return ""
    if "text" in first:
        return str(first.get("text") or "")

    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if content:
            return str(content)
        reasoning = message.get("reasoning_content")
        if reasoning:
            return str(reasoning)
        if content is not None:
            return str(content)
        tool_calls = message.get("tool_calls")
        if tool_calls is not None:
            try:
                return json.dumps({"tool_calls": tool_calls})
            except Exception:
                return ""
    return ""


def _extract_lmstudio_usage(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {}
    usage = payload.get("usage")
    if isinstance(usage, dict):
        return usage
    return {}
