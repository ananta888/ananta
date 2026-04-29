from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from worker.retrieval.embedding_provider import (
    EmbeddingProviderError,
    build_embedding_provider,
)

_DEFAULT_ENUM_CANDIDATES: dict[str, list[str]] = {
    "risk_classification": ["low", "medium", "high", "critical"],
}


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    numerator = sum(float(a) * float(b) for a, b in zip(left, right, strict=False))
    left_norm = sum(float(a) * float(a) for a in left) ** 0.5
    right_norm = sum(float(b) * float(b) for b in right) ** 0.5
    if left_norm <= 1e-9 or right_norm <= 1e-9:
        return 0.0
    return float(numerator / (left_norm * right_norm))


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        normalized = float(default)
    return min(maximum, max(minimum, normalized))


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        normalized = int(default)
    return min(maximum, max(minimum, normalized))


def normalize_semantic_correction_policy(value: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(value or {})
    provider_cfg = payload.get("embedding_provider")
    provider_cfg = dict(provider_cfg) if isinstance(provider_cfg, dict) else {}
    provider = str(provider_cfg.get("provider") or "local").strip().lower() or "local"
    model_version = str(provider_cfg.get("model_version") or "").strip() or None
    normalized: dict[str, Any] = {
        "enabled": bool(payload.get("enabled", False)),
        "similarity_threshold": _bounded_float(
            payload.get("similarity_threshold"),
            default=0.9,
            minimum=0.5,
            maximum=1.0,
        ),
        "min_margin": _bounded_float(payload.get("min_margin"), default=0.03, minimum=0.0, maximum=1.0),
        "lexical_weight": _bounded_float(payload.get("lexical_weight"), default=0.35, minimum=0.0, maximum=1.0),
        "embedding_provider": {
            "provider": provider,
            "dimensions": _bounded_int(provider_cfg.get("dimensions"), default=12, minimum=4, maximum=4096),
            "model_version": model_version,
            "base_url": str(provider_cfg.get("base_url") or "").strip() or None,
            "api_key": str(provider_cfg.get("api_key") or "").strip() or None,
            "model": str(provider_cfg.get("model") or "").strip() or None,
            "timeout_seconds": _bounded_int(provider_cfg.get("timeout_seconds"), default=20, minimum=1, maximum=120),
        },
        "fields": {},
    }
    raw_fields = payload.get("fields")
    raw_fields = dict(raw_fields) if isinstance(raw_fields, dict) else {}
    normalized_fields: dict[str, Any] = {}
    for field_name, default_candidates in _DEFAULT_ENUM_CANDIDATES.items():
        field_cfg = raw_fields.get(field_name)
        field_cfg = dict(field_cfg) if isinstance(field_cfg, dict) else {}
        candidates = [
            str(item).strip().lower()
            for item in list(field_cfg.get("candidates") or default_candidates)
            if str(item).strip()
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for item in candidates:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        normalized_fields[field_name] = {
            "enabled": bool(field_cfg.get("enabled", True)),
            "candidates": deduped,
        }
    normalized["fields"] = normalized_fields
    return normalized


def _embedding_provider_config(policy: dict[str, Any]) -> dict[str, Any]:
    provider_cfg = dict(policy.get("embedding_provider") or {})
    provider = str(provider_cfg.get("provider") or "local").strip().lower() or "local"
    config: dict[str, Any] = {
        "provider": provider,
        "dimensions": int(provider_cfg.get("dimensions") or 12),
    }
    model_version = str(provider_cfg.get("model_version") or "").strip()
    if model_version:
        config["model_version"] = model_version
    if provider in {"openai", "openai_compatible"}:
        base_url = str(provider_cfg.get("base_url") or "").strip()
        api_key = str(provider_cfg.get("api_key") or "").strip()
        model = str(provider_cfg.get("model") or "").strip()
        timeout_seconds = int(provider_cfg.get("timeout_seconds") or 20)
        if base_url:
            config["base_url"] = base_url
        if api_key:
            config["api_key"] = api_key
        if model:
            config["model"] = model
        config["timeout_seconds"] = timeout_seconds
    return config


def _combined_similarity(
    *,
    source: str,
    candidate: str,
    source_vector: list[float],
    candidate_vector: list[float],
    lexical_weight: float,
) -> dict[str, float]:
    vector_similarity = _cosine_similarity(source_vector, candidate_vector)
    lexical_similarity = SequenceMatcher(a=source, b=candidate).ratio()
    combined = ((1.0 - lexical_weight) * vector_similarity) + (lexical_weight * lexical_similarity)
    return {
        "vector_similarity": float(vector_similarity),
        "lexical_similarity": float(lexical_similarity),
        "combined_similarity": float(combined),
    }


def correct_semantic_enum_fields(
    *,
    payload: dict[str, Any],
    policy: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    normalized_policy = normalize_semantic_correction_policy(policy)
    if not bool(normalized_policy.get("enabled")):
        return dict(payload or {}), None

    fields_cfg = dict(normalized_policy.get("fields") or {})
    active_fields = {
        field_name: dict(field_cfg)
        for field_name, field_cfg in fields_cfg.items()
        if isinstance(field_cfg, dict) and bool(field_cfg.get("enabled", True)) and list(field_cfg.get("candidates") or [])
    }
    if not active_fields:
        return dict(payload or {}), None

    working_payload = dict(payload or {})
    lexical_weight = float(normalized_policy.get("lexical_weight") or 0.35)
    threshold = float(normalized_policy.get("similarity_threshold") or 0.9)
    min_margin = float(normalized_policy.get("min_margin") or 0.03)

    values: list[str] = []
    value_index: dict[str, int] = {}
    candidate_vectors_by_field: dict[str, list[list[float]]] = {}
    for field_name, field_cfg in active_fields.items():
        raw_value = str(working_payload.get(field_name) or "").strip().lower()
        if not raw_value:
            continue
        candidates = [str(item).strip().lower() for item in list(field_cfg.get("candidates") or []) if str(item).strip()]
        if not candidates:
            continue
        value_index[field_name] = len(values)
        values.append(raw_value)
        values.extend(candidates)
        candidate_vectors_by_field[field_name] = []

    if not value_index:
        return working_payload, None

    try:
        embedding_provider = build_embedding_provider(_embedding_provider_config(normalized_policy))
        vectors = embedding_provider.embed_texts(values)
    except (ValueError, EmbeddingProviderError) as exc:
        return working_payload, {
            "enabled": True,
            "applied": False,
            "reason": "embedding_provider_unavailable",
            "error": str(exc),
        }

    field_reports: list[dict[str, Any]] = []
    for field_name, field_cfg in active_fields.items():
        if field_name not in value_index:
            continue
        source_index = value_index[field_name]
        candidates = [str(item).strip().lower() for item in list(field_cfg.get("candidates") or []) if str(item).strip()]
        source_value = str(working_payload.get(field_name) or "").strip().lower()
        if source_value in set(candidates):
            field_reports.append(
                {
                    "field": field_name,
                    "status": "unchanged",
                    "input": source_value,
                    "resolved": source_value,
                    "reason": "already_valid",
                }
            )
            continue
        source_vector = [float(item) for item in list(vectors[source_index] if source_index < len(vectors) else [])]
        scored: list[tuple[str, dict[str, float]]] = []
        for candidate_offset, candidate in enumerate(candidates, start=1):
            vector_index = source_index + candidate_offset
            candidate_vector = [float(item) for item in list(vectors[vector_index] if vector_index < len(vectors) else [])]
            scored.append(
                (
                    candidate,
                    _combined_similarity(
                        source=source_value,
                        candidate=candidate,
                        source_vector=source_vector,
                        candidate_vector=candidate_vector,
                        lexical_weight=lexical_weight,
                    ),
                )
            )
        scored.sort(key=lambda item: float(item[1]["combined_similarity"]), reverse=True)
        best_candidate, best_scores = scored[0]
        second_score = float(scored[1][1]["combined_similarity"]) if len(scored) > 1 else 0.0
        margin = float(best_scores["combined_similarity"]) - second_score
        if float(best_scores["combined_similarity"]) >= threshold and margin >= min_margin:
            working_payload[field_name] = best_candidate
            field_reports.append(
                {
                    "field": field_name,
                    "status": "corrected",
                    "input": source_value,
                    "resolved": best_candidate,
                    "combined_similarity": round(float(best_scores["combined_similarity"]), 4),
                    "margin": round(float(margin), 4),
                }
            )
            continue
        field_reports.append(
            {
                "field": field_name,
                "status": "unchanged",
                "input": source_value,
                "resolved": source_value,
                "reason": "below_threshold",
                "combined_similarity": round(float(best_scores["combined_similarity"]), 4),
                "margin": round(float(margin), 4),
            }
        )

    return working_payload, {
        "enabled": True,
        "applied": any(str(item.get("status") or "") == "corrected" for item in field_reports),
        "similarity_threshold": threshold,
        "min_margin": min_margin,
        "provider": str((normalized_policy.get("embedding_provider") or {}).get("provider") or "local"),
        "fields": field_reports,
    }
