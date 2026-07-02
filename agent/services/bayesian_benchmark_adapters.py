"""
Evidence extraction adapters for hub, Ollama, and llm_benchmarks systems.

Each adapter reads existing benchmark sample lists and converts them to
BayesianEvidence records without altering the source persistence format.
Filters mirror the filter parameters already supported by the corresponding
recommend_* functions so evidence counts match recommendation sample counts.
"""
from __future__ import annotations

from typing import Any

from agent.services.bayesian_benchmark_estimator import (
    BayesianEvidence,
    estimate_bayesian_for_samples,
    normalize_sample_to_evidence,
)


# ── Hub benchmark adapter ──────────────────────────────────────────────────────

def extract_hub_evidence(
    bucket: dict[str, Any],
    *,
    provider: str = "",
    model: str = "",
    role_name_filter: str | None = None,
    task_kind_filter: str | None = None,
) -> list[BayesianEvidence]:
    """
    Extract BayesianEvidence records from a hub benchmark bucket's sample list.

    Filters mirror recommend_hub_models() so evidence counts match the
    sample counts used by existing recommendation logic.
    """
    if not isinstance(bucket, dict):
        return []
    samples = bucket.get("samples") or []
    if not isinstance(samples, list):
        return []

    role_match = str(role_name_filter or "").strip().lower()
    task_match = str(task_kind_filter or "").strip().lower()
    evidence: list[BayesianEvidence] = []

    for sample in samples:
        if not isinstance(sample, dict):
            continue
        if role_match and str(sample.get("role_name") or "").strip().lower() != role_match:
            continue
        if task_match and str(sample.get("task_kind") or "").strip().lower() != task_match:
            continue
        evidence.append(
            normalize_sample_to_evidence(
                sample,
                source="hub",
                provider=provider or None,
                model=model,
            )
        )
    return evidence


def enrich_hub_score_with_bayes(
    score: dict[str, Any],
    samples: list[dict[str, Any]],
    *,
    provider: str = "",
    model: str = "",
    **estimator_kwargs: Any,
) -> dict[str, Any]:
    """
    Return a copy of *score* extended with a ``bayesian_estimate`` field.

    The original dict is not mutated.
    """
    result = dict(score)
    result["bayesian_estimate"] = estimate_bayesian_for_samples(
        samples,
        source="hub",
        provider=provider or None,
        model=model,
        **estimator_kwargs,
    )
    return result


# ── Ollama benchmark adapter ───────────────────────────────────────────────────

def extract_ollama_evidence(
    bucket: dict[str, Any],
    *,
    model: str = "",
    role_name_filter: str | None = None,
    task_kind_filter: str | None = None,
    parameter_filter: dict[str, Any] | None = None,
) -> list[BayesianEvidence]:
    """
    Extract BayesianEvidence records from an Ollama benchmark bucket.

    Parameter variations are preserved in the ``parameters`` field of each
    evidence record, keeping provider/model and parameter-set separation
    intact for downstream Bayesian slicing.

    Filters mirror recommend_ollama_models() including preferred_parameters.
    """
    if not isinstance(bucket, dict):
        return []
    samples = bucket.get("samples") or []
    if not isinstance(samples, list):
        return []

    role_match = str(role_name_filter or "").strip().lower()
    task_match = str(task_kind_filter or "").strip().lower()
    evidence: list[BayesianEvidence] = []

    for sample in samples:
        if not isinstance(sample, dict):
            continue
        if role_match and str(sample.get("role_name") or "").strip().lower() != role_match:
            continue
        if task_match and str(sample.get("task_kind") or "").strip().lower() != task_match:
            continue
        if parameter_filter:
            sample_params = sample.get("parameters") or {}
            if not all(sample_params.get(k) == v for k, v in parameter_filter.items()):
                continue
        evidence.append(
            normalize_sample_to_evidence(
                sample,
                source="ollama",
                provider=None,
                model=model,
            )
        )
    return evidence


def enrich_ollama_score_with_bayes(
    score: dict[str, Any],
    samples: list[dict[str, Any]],
    *,
    model: str = "",
    **estimator_kwargs: Any,
) -> dict[str, Any]:
    """Return a copy of *score* extended with a ``bayesian_estimate`` field."""
    result = dict(score)
    result["bayesian_estimate"] = estimate_bayesian_for_samples(
        samples,
        source="ollama",
        provider=None,
        model=model,
        **estimator_kwargs,
    )
    return result


# ── llm_benchmarks adapter ─────────────────────────────────────────────────────

def extract_llm_benchmark_evidence(
    bucket: dict[str, Any],
    *,
    provider: str = "",
    model: str = "",
    task_kind_filter: str | None = None,
    role_name_filter: str | None = None,
    template_name_filter: str | None = None,
) -> list[BayesianEvidence]:
    """
    Extract BayesianEvidence records from a llm_benchmarks task-kind bucket.

    Context-based filtering (role_name, template_name) mirrors
    recommend_models_for_context() so evidence counts match recommendation
    sample counts.
    """
    if not isinstance(bucket, dict):
        return []
    samples = bucket.get("samples") or []
    if not isinstance(samples, list):
        return []

    task_match = str(task_kind_filter or "").strip().lower()
    role_match = str(role_name_filter or "").strip().lower()
    template_match = str(template_name_filter or "").strip().lower()
    evidence: list[BayesianEvidence] = []

    for sample in samples:
        if not isinstance(sample, dict):
            continue
        context = sample.get("context") if isinstance(sample.get("context"), dict) else {}
        sample_role = str(context.get("role_name") or "").strip().lower()
        sample_template = str(context.get("template_name") or "").strip().lower()
        sample_task = str(sample.get("task_kind") or "").strip().lower()

        if task_match and sample_task and sample_task != task_match:
            continue
        if role_match and sample_role != role_match:
            continue
        if template_match and sample_template != template_match:
            continue

        # Flatten context fields into sample for normalisation
        flat = dict(sample)
        if not flat.get("role_name") and context.get("role_name"):
            flat["role_name"] = context["role_name"]

        evidence.append(
            normalize_sample_to_evidence(
                flat,
                source="llm_benchmark",
                provider=provider or None,
                model=model,
            )
        )
    return evidence


def enrich_llm_benchmark_score_with_bayes(
    score: dict[str, Any],
    samples: list[dict[str, Any]],
    *,
    provider: str = "",
    model: str = "",
    **estimator_kwargs: Any,
) -> dict[str, Any]:
    """Return a copy of *score* extended with a ``bayesian_estimate`` field."""
    result = dict(score)
    result["bayesian_estimate"] = estimate_bayesian_for_samples(
        samples,
        source="llm_benchmark",
        provider=provider or None,
        model=model,
        **estimator_kwargs,
    )
    return result
