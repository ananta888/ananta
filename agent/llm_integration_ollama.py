from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from agent.llm_integration_lmstudio import _model_identifier_matches, _model_identifier_tokens
from agent.utils import _http_get


def _find_matching_ollama_candidate(model: str | None, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized_model = str(model or "").strip()
    if not normalized_model:
        return None
    for candidate in candidates:
        candidate_name = str((candidate or {}).get("name") or "").strip()
        if _model_identifier_matches(normalized_model, candidate_name):
            return candidate
    if normalized_model.lower() == "qwen2.5-coder:7b":
        for candidate in candidates:
            candidate_name = str((candidate or {}).get("name") or "").strip().lower()
            if candidate_name == "ananta-default:latest":
                return candidate
    return None


def _normalize_ollama_base_url(base_url: str | None) -> Optional[str]:
    raw_url = str(base_url or "").strip()
    if not raw_url:
        return None

    normalized = raw_url.rstrip("/")
    normalized_lower = normalized.lower()
    for suffix in ("/api/generate", "/api/chat", "/api/tags"):
        if normalized_lower.endswith(suffix):
            normalized = normalized[: -len(suffix)]
            break

    parsed = urlsplit(normalized)
    if not parsed.scheme or not parsed.netloc:
        return None

    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _ollama_tags_url(base_url: str) -> Optional[str]:
    normalized = _normalize_ollama_base_url(base_url)
    if not normalized:
        return None
    return f"{normalized}/api/tags"


def _ollama_ps_url(base_url: str) -> Optional[str]:
    normalized = _normalize_ollama_base_url(base_url)
    if not normalized:
        return None
    return f"{normalized}/api/ps"


def resolve_ollama_model(model: str | None, base_url: str, timeout: int) -> Optional[str]:
    normalized_model = str(model or "").strip()
    if not normalized_model:
        return None
    probe = probe_ollama_runtime(base_url, timeout)
    candidates = list(probe.get("models") or []) if isinstance(probe, dict) else []
    matched = _find_matching_ollama_candidate(normalized_model, candidates)
    if matched:
        candidate_name = str((matched or {}).get("name") or "").strip()
        if candidate_name:
            return candidate_name
    return normalized_model


def probe_ollama_runtime(base_url: str, timeout: int) -> dict[str, Any]:
    tags_url = _ollama_tags_url(base_url)
    if not tags_url:
        return {
            "ok": False,
            "status": "invalid_url",
            "base_url": base_url,
            "tags_url": None,
            "models": [],
            "candidate_count": 0,
        }
    try:
        resp = _http_get(tags_url, timeout=timeout, silent=True)
    except Exception:
        return {
            "ok": False,
            "status": "error",
            "base_url": base_url,
            "tags_url": tags_url,
            "models": [],
            "candidate_count": 0,
        }

    raw_models = resp.get("models") if isinstance(resp, dict) else None
    models = [item for item in (raw_models or []) if isinstance(item, dict) and str(item.get("name") or "").strip()]
    status = "ok" if models else "reachable_no_models"
    return {
        "ok": True,
        "status": status,
        "base_url": base_url,
        "tags_url": tags_url,
        "models": models,
        "candidate_count": len(models),
    }


def probe_ollama_activity(base_url: str, timeout: int) -> dict[str, Any]:
    ps_url = _ollama_ps_url(base_url)
    if not ps_url:
        return {
            "ok": False,
            "status": "invalid_url",
            "base_url": base_url,
            "ps_url": None,
            "active_count": 0,
            "active_models": [],
            "gpu_active": False,
            "executor_summary": {"gpu": 0, "cpu": 0, "unknown": 0},
        }
    try:
        resp = _http_get(ps_url, timeout=timeout, silent=True)
    except Exception:
        return {
            "ok": False,
            "status": "error",
            "base_url": base_url,
            "ps_url": ps_url,
            "active_count": 0,
            "active_models": [],
            "gpu_active": False,
            "executor_summary": {"gpu": 0, "cpu": 0, "unknown": 0},
        }

    raw_models = resp.get("models") if isinstance(resp, dict) else None
    models = [item for item in (raw_models or []) if isinstance(item, dict) and str(item.get("name") or "").strip()]
    active_models: list[dict[str, Any]] = []
    summary = {"gpu": 0, "cpu": 0, "unknown": 0}
    for item in models:
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        size_vram = int(item.get("size_vram") or 0)
        processor = str(item.get("processor") or "").strip().lower()
        if not processor:
            processor = str(details.get("processor") or "").strip().lower()
        if processor in {"gpu", "cuda", "vulkan", "metal"} or size_vram > 0:
            executor = "gpu"
        elif processor in {"cpu"}:
            executor = "cpu"
        else:
            executor = "unknown"
        summary[executor] = int(summary.get(executor) or 0) + 1
        active_models.append(
            {
                "name": str(item.get("name") or "").strip(),
                "size": int(item.get("size") or 0),
                "size_vram": size_vram,
                "expires_at": item.get("expires_at"),
                "executor": executor,
                "context_length": item.get("context_length") or item.get("num_ctx") or details.get("num_ctx"),
            }
        )
    return {
        "ok": True,
        "status": "ok" if active_models else "reachable_no_active_models",
        "base_url": base_url,
        "ps_url": ps_url,
        "active_count": len(active_models),
        "active_models": active_models,
        "gpu_active": bool(summary.get("gpu")),
        "executor_summary": summary,
    }
