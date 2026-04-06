from __future__ import annotations

import json
import os
import time
from typing import Any


HUB_BENCH_TASK_KINDS = {"planning", "research", "coding", "review", "testing", "ops", "analysis", "doc"}
DEFAULT_HUB_BENCH_CONFIG = {
    "enabled": True,
    "auto_trigger": {"enabled": True, "min_samples_before_auto": 3, "interval_hours": 24, "max_execution_minutes": 30},
    "scoring": {
        "weights": {"success_rate": 0.40, "quality_rate": 0.35, "latency_score": 0.15, "cost_score": 0.10},
        "thresholds": {"min_samples": 2, "min_success_rate": 0.5},
    },
    "retention": {"max_samples_per_model": 100, "max_days": 30},
}


def hub_benchmark_config_path(data_dir: str, filename: str = "hub_benchmark_config.json") -> str:
    config_path = os.path.join(data_dir, filename)
    return config_path


def hub_benchmark_results_path(data_dir: str, filename: str = "hub_benchmark_results.json") -> str:
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, filename)


def load_hub_benchmark_config(data_dir: str) -> dict[str, Any]:
    path = hub_benchmark_config_path(data_dir)
    default_cfg = dict(DEFAULT_HUB_BENCH_CONFIG)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
            if isinstance(loaded, dict):
                for key, value in default_cfg.items():
                    if key not in loaded:
                        loaded[key] = value
                return loaded
    except Exception:
        pass
    return default_cfg


def load_hub_benchmark_results(data_dir: str) -> dict[str, Any]:
    path = hub_benchmark_results_path(data_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"models": {}, "updated_at": None, "last_benchmark_run": None}


def save_hub_benchmark_results(data_dir: str, data: dict[str, Any]) -> None:
    path = hub_benchmark_results_path(data_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def default_hub_metric_bucket() -> dict[str, Any]:
    return {
        "total": 0,
        "success": 0,
        "failed": 0,
        "quality_pass": 0,
        "quality_fail": 0,
        "latency_ms_total": 0,
        "tokens_total": 0,
        "cost_units_total": 0.0,
        "last_seen": None,
        "role_scores": {},
    }


def score_hub_bucket(bucket: dict[str, Any], weights: dict[str, float] | None = None) -> dict[str, Any]:
    weights = weights or {"success_rate": 0.40, "quality_rate": 0.35, "latency_score": 0.15, "cost_score": 0.10}
    total = max(0, int(bucket.get("total") or 0))
    success = max(0, int(bucket.get("success") or 0))
    quality_pass = max(0, int(bucket.get("quality_pass") or 0))
    latency_ms_total = max(0, int(bucket.get("latency_ms_total") or 0))
    tokens_total = max(0, int(bucket.get("tokens_total") or 0))
    cost_units_total = max(0.0, float(bucket.get("cost_units_total") or 0.0))
    success_rate = (success / total) if total else 0.0
    quality_rate = (quality_pass / total) if total else 0.0
    avg_latency_ms = (latency_ms_total / total) if total else 0.0
    avg_tokens = (tokens_total / total) if total else 0.0
    avg_cost_units = (cost_units_total / total) if total else 0.0
    latency_score = max(0.0, min(1.0, 1.0 - (avg_latency_ms / 60000.0)))
    cost_score = max(0.0, min(1.0, 1.0 - (avg_cost_units / 0.01)))
    suitability_score = round(
        (
            weights.get("success_rate", 0.40) * success_rate
            + weights.get("quality_rate", 0.35) * quality_rate
            + weights.get("latency_score", 0.15) * latency_score
            + weights.get("cost_score", 0.10) * cost_score
        )
        * 100.0,
        2,
    )
    return {
        "total": total,
        "success_rate": round(success_rate, 4),
        "quality_rate": round(quality_rate, 4),
        "avg_latency_ms": round(avg_latency_ms, 2),
        "avg_tokens": round(avg_tokens, 2),
        "avg_cost_units": round(avg_cost_units, 6),
        "cost_units_total": round(cost_units_total, 6),
        "suitability_score": suitability_score,
    }


def record_hub_benchmark_sample(
    *,
    data_dir: str,
    provider: str,
    model: str,
    role_name: str,
    task_kind: str,
    success: bool,
    quality_gate_passed: bool,
    latency_ms: int,
    tokens_total: int,
    cost_units: float = 0.0,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    role_name = str(role_name or "").strip().lower()
    task_kind = str(task_kind or "analysis").strip().lower()
    if task_kind not in HUB_BENCH_TASK_KINDS:
        task_kind = "analysis"
    if not provider or not model:
        return {"recorded": False}
    cfg = load_hub_benchmark_config(data_dir)
    retention = cfg.get("retention", {})
    max_samples = int(retention.get("max_samples_per_model", 100))
    max_days = int(retention.get("max_days", 30))
    db = load_hub_benchmark_results(data_dir)
    models = db.setdefault("models", {})
    model_key = f"{provider}:{model}"
    entry = models.setdefault(
        model_key, {"provider": provider, "model": model, "overall": default_hub_metric_bucket(), "roles": {}}
    )
    role_bucket = (entry.setdefault("roles", {})).setdefault(role_name, default_hub_metric_bucket())
    now = int(time.time())
    min_ts = now - (max_days * 86400)

    def _apply(target: dict[str, Any]) -> None:
        target["total"] = int(target.get("total") or 0) + 1
        target["success"] = int(target.get("success") or 0) + (1 if success else 0)
        target["failed"] = int(target.get("failed") or 0) + (0 if success else 1)
        target["quality_pass"] = int(target.get("quality_pass") or 0) + (1 if quality_gate_passed else 0)
        target["quality_fail"] = int(target.get("quality_fail") or 0) + (0 if quality_gate_passed else 1)
        target["latency_ms_total"] = int(target.get("latency_ms_total") or 0) + max(0, int(latency_ms or 0))
        target["tokens_total"] = int(target.get("tokens_total") or 0) + max(0, int(tokens_total or 0))
        target["cost_units_total"] = float(target.get("cost_units_total") or 0.0) + float(cost_units or 0.0)
        target["last_seen"] = now
        samples = target.setdefault("samples", [])
        if not isinstance(samples, list):
            samples = []
            target["samples"] = samples
        else:
            samples[:] = [s for s in samples if int((s or {}).get("ts") or 0) >= min_ts]
        sample = {
            "ts": now,
            "role_name": role_name,
            "task_kind": task_kind,
            "success": bool(success),
            "quality_passed": bool(quality_gate_passed),
            "latency_ms": max(0, int(latency_ms or 0)),
            "tokens_total": max(0, int(tokens_total or 0)),
            "cost_units": max(0.0, float(cost_units or 0.0)),
        }
        samples.append(sample)
        if len(samples) > max_samples:
            del samples[: len(samples) - max_samples]

    _apply(role_bucket)
    _apply(entry.setdefault("overall", default_hub_metric_bucket()))
    db["updated_at"] = now
    save_hub_benchmark_results(data_dir, db)
    return {"recorded": True, "model_key": model_key, "role_name": role_name, "db": db}


def recommend_hub_model(
    *,
    data_dir: str,
    role_name: str | None = None,
    task_kind: str | None = None,
    min_samples: int = 2,
    exclude_models: list[str] | None = None,
) -> dict[str, Any] | None:
    ranked = recommend_hub_models(
        data_dir=data_dir,
        role_name=role_name,
        task_kind=task_kind,
        min_samples=min_samples,
        limit=1,
        exclude_models=exclude_models,
    )
    if not ranked:
        return None
    best = dict(ranked[0] or {})
    best["selection_source"] = "hub_benchmark"
    return best


def recommend_hub_models(
    *,
    data_dir: str,
    role_name: str | None = None,
    task_kind: str | None = None,
    min_samples: int = 2,
    limit: int = 3,
    exclude_models: list[str] | None = None,
) -> list[dict[str, Any]]:
    role_match = str(role_name or "").strip().lower()
    excluded = {str(item or "").strip() for item in list(exclude_models or []) if str(item or "").strip()}
    cfg = load_hub_benchmark_config(data_dir)
    weights = (cfg.get("scoring", {}) or {}).get("weights", {})
    db = load_hub_benchmark_results(data_dir)
    candidates: list[dict[str, Any]] = []

    for model_key, entry in (db.get("models") or {}).items():
        if not isinstance(entry, dict):
            continue
        model = str(entry.get("model") or "").strip()
        provider = str(entry.get("provider") or "").strip().lower()
        if not provider or not model or model in excluded:
            continue
        if role_match:
            role_bucket = (entry.get("roles") or {}).get(role_match) or {}
        else:
            role_bucket = entry.get("overall") or {}
        samples = list(role_bucket.get("samples") or []) if isinstance(role_bucket, dict) else []
        if not samples:
            continue
        filtered = []
        for sample in samples:
            if not isinstance(sample, dict):
                continue
            if role_match and str(sample.get("role_name") or "").strip().lower() != role_match:
                continue
            if task_kind and str(sample.get("task_kind") or "").strip().lower() != task_kind:
                continue
            filtered.append(sample)
        if len(filtered) < max(1, int(min_samples or 1)):
            continue
        aggregate = {
            "total": len(filtered),
            "success": sum(1 for s in filtered if bool(s.get("success"))),
            "quality_pass": sum(1 for s in filtered if bool(s.get("quality_passed"))),
            "latency_ms_total": sum(max(0, int(s.get("latency_ms") or 0)) for s in filtered),
            "tokens_total": sum(max(0, int(s.get("tokens_total") or 0)) for s in filtered),
            "cost_units_total": sum(max(0.0, float(s.get("cost_units") or 0.0)) for s in filtered),
        }
        scored = score_hub_bucket(aggregate, weights)
        candidate = {
            "provider": provider,
            "model": model,
            "role_name": role_match or None,
            "task_kind": task_kind,
            "sample_count": aggregate["total"],
            "score": scored,
        }
        candidates.append(candidate)

    candidates.sort(key=lambda item: float(((item.get("score") or {}).get("suitability_score") or 0.0)), reverse=True)
    capped = max(1, min(int(limit or 1), 10))
    return candidates[:capped]


def hub_benchmark_rows(
    *,
    data_dir: str,
    role_name: str | None = None,
    top_n: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    role_match = str(role_name or "").strip().lower()
    cfg = load_hub_benchmark_config(data_dir)
    weights = (cfg.get("scoring", {}) or {}).get("weights", {})
    db = load_hub_benchmark_results(data_dir)
    rows: list[dict[str, Any]] = []

    for key, entry in (db.get("models") or {}).items():
        if not isinstance(entry, dict):
            continue
        overall = score_hub_bucket(entry.get("overall") or {}, weights)
        row = {
            "id": key,
            "provider": str(entry.get("provider") or "").strip().lower(),
            "model": str(entry.get("model") or "").strip(),
            "overall": overall,
            "roles": {},
        }
        for role, role_data in (entry.get("roles") or {}).items():
            row["roles"][role] = score_hub_bucket(role_data or {}, weights)
        if role_match and role_match in row["roles"]:
            row["focus"] = row["roles"][role_match]
        else:
            row["focus"] = overall
        row["_sort_score"] = float((row["focus"] or {}).get("suitability_score") or 0.0)
        rows.append(row)

    rows.sort(key=lambda item: item.get("_sort_score") or 0.0, reverse=True)
    if isinstance(top_n, int) and top_n > 0:
        rows = rows[:top_n]
    for row in rows:
        row.pop("_sort_score", None)
    return rows, db


def check_auto_trigger_needed(data_dir: str) -> tuple[bool, str | None]:
    cfg = load_hub_benchmark_config(data_dir)
    if not cfg.get("enabled", True):
        return False, "disabled"
    auto_cfg = cfg.get("auto_trigger", {})
    if not auto_cfg.get("enabled", True):
        return False, "auto_trigger_disabled"
    min_samples = int(auto_cfg.get("min_samples_before_auto", 3))
    interval_hours = int(auto_cfg.get("interval_hours", 24))
    db = load_hub_benchmark_results(data_dir)
    last_run = db.get("last_benchmark_run")
    if last_run:
        last_ts = int(last_run)
        elapsed_hours = (int(time.time()) - last_ts) / 3600.0
        if elapsed_hours < interval_hours:
            return False, f"too_recent_{elapsed_hours:.1f}h"
    models = db.get("models", {})
    total_samples = sum(
        int((entry.get("overall") or {}).get("total", 0)) for entry in models.values() if isinstance(entry, dict)
    )
    if total_samples < min_samples:
        return False, f"insufficient_samples_{total_samples}"
    return True, None


def update_benchmark_run_timestamp(data_dir: str, timestamp: int | None = None) -> None:
    db = load_hub_benchmark_results(data_dir)
    db["last_benchmark_run"] = timestamp or int(time.time())
    save_hub_benchmark_results(data_dir, db)
