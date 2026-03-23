from __future__ import annotations

import json
import os
import time
from typing import Any


BENCH_TASK_KINDS = {"coding", "analysis", "doc", "ops", "research"}
DEFAULT_BENCH_RETENTION = {"max_samples": 2000, "max_days": 90}
DEFAULT_BENCH_PROVIDER_ORDER = [
    "proposal_backend",
    "routing_effective_backend",
    "llm_config_provider",
    "default_provider",
    "provider",
]
DEFAULT_BENCH_MODEL_ORDER = [
    "proposal_model",
    "llm_config_model",
    "default_model",
    "model",
]


def benchmarks_path(data_dir: str, filename: str = "llm_model_benchmarks.json") -> str:
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, filename)


def benchmark_retention_config(agent_cfg: dict | None) -> dict[str, int]:
    cfg = (agent_cfg or {}).get("benchmark_retention", {}) or {}
    return {
        "max_samples": max(50, min(50000, int(cfg.get("max_samples") or DEFAULT_BENCH_RETENTION["max_samples"]))),
        "max_days": max(1, min(3650, int(cfg.get("max_days") or DEFAULT_BENCH_RETENTION["max_days"]))),
    }


def benchmark_identity_precedence_config(agent_cfg: dict | None) -> dict[str, list[str]]:
    cfg = (agent_cfg or {}).get("benchmark_identity_precedence", {}) or {}
    allowed_provider_sources = {
        "proposal_backend",
        "routing_effective_backend",
        "llm_config_provider",
        "default_provider",
        "provider",
    }
    allowed_model_sources = {
        "proposal_model",
        "llm_config_model",
        "default_model",
        "model",
    }
    provider_order = [
        str(x).strip().lower()
        for x in (cfg.get("provider_order") if isinstance(cfg.get("provider_order"), list) else DEFAULT_BENCH_PROVIDER_ORDER)
        if str(x).strip().lower() in allowed_provider_sources
    ]
    model_order = [
        str(x).strip().lower()
        for x in (cfg.get("model_order") if isinstance(cfg.get("model_order"), list) else DEFAULT_BENCH_MODEL_ORDER)
        if str(x).strip().lower() in allowed_model_sources
    ]
    return {
        "provider_order": provider_order or list(DEFAULT_BENCH_PROVIDER_ORDER),
        "model_order": model_order or list(DEFAULT_BENCH_MODEL_ORDER),
    }


def default_metric_bucket() -> dict[str, Any]:
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
    }


def load_benchmarks(data_dir: str) -> dict[str, Any]:
    path = benchmarks_path(data_dir)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"models": {}, "updated_at": None}


def save_benchmarks(data_dir: str, data: dict[str, Any]) -> None:
    path = benchmarks_path(data_dir)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def append_sample(
    target: dict[str, Any],
    *,
    now: int,
    success: bool,
    quality_passed: bool,
    latency_ms: int,
    tokens_total: int,
    retention: dict[str, int],
) -> None:
    min_ts = int(now) - (int(retention["max_days"]) * 86400)
    samples = target.setdefault("samples", [])
    if not isinstance(samples, list):
        samples = []
        target["samples"] = samples
    else:
        samples[:] = [s for s in samples if int((s or {}).get("ts") or 0) >= min_ts]
    samples.append(
        {
            "ts": int(now),
            "success": bool(success),
            "quality_passed": bool(quality_passed),
            "latency_ms": max(0, int(latency_ms or 0)),
            "tokens_total": max(0, int(tokens_total or 0)),
        }
    )
    if len(samples) > int(retention["max_samples"]):
        del samples[: len(samples) - int(retention["max_samples"])]


def record_benchmark_sample(
    *,
    data_dir: str,
    agent_cfg: dict | None,
    provider: str,
    model: str,
    task_kind: str,
    success: bool,
    quality_gate_passed: bool,
    latency_ms: int,
    tokens_total: int,
    cost_units: float = 0.0,
) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    model = str(model or "").strip()
    task_kind = str(task_kind or "analysis").strip().lower()
    if not provider or not model:
        return {"recorded": False}
    if task_kind not in BENCH_TASK_KINDS:
        task_kind = "analysis"

    db = load_benchmarks(data_dir)
    models = db.setdefault("models", {})
    model_key = f"{provider}:{model}"
    entry = models.setdefault(
        model_key,
        {"provider": provider, "model": model, "overall": default_metric_bucket(), "task_kinds": {}},
    )
    bucket = (entry.setdefault("task_kinds", {})).setdefault(task_kind, default_metric_bucket())
    retention = benchmark_retention_config(agent_cfg)
    now = int(time.time())

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
        append_sample(
            target,
            now=now,
            success=success,
            quality_passed=quality_gate_passed,
            latency_ms=latency_ms,
            tokens_total=tokens_total,
            retention=retention,
        )

    _apply(bucket)
    _apply(entry.setdefault("overall", default_metric_bucket()))
    db["updated_at"] = now
    save_benchmarks(data_dir, db)
    return {"recorded": True, "model_key": model_key, "task_kind": task_kind, "db": db}


def score_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    total = max(0, int(bucket.get("total") or 0))
    success = max(0, int(bucket.get("success") or 0))
    quality_pass = max(0, int(bucket.get("quality_pass") or 0))
    latency_ms_total = max(0, int(bucket.get("latency_ms_total") or 0))
    tokens_total = max(0, int(bucket.get("tokens_total") or 0))
    success_rate = (success / total) if total else 0.0
    quality_rate = (quality_pass / total) if total else 0.0
    avg_latency_ms = (latency_ms_total / total) if total else 0.0
    avg_tokens = (tokens_total / total) if total else 0.0
    latency_score = max(0.0, min(1.0, 1.0 - (avg_latency_ms / 30000.0)))
    token_score = max(0.0, min(1.0, 1.0 - (avg_tokens / 8000.0)))
    efficiency = (latency_score + token_score) / 2.0
    suitability_score = round((0.45 * success_rate + 0.35 * quality_rate + 0.20 * efficiency) * 100.0, 2)
    return {
        "total": total,
        "success_rate": round(success_rate, 4),
        "quality_rate": round(quality_rate, 4),
        "avg_latency_ms": round(avg_latency_ms, 2),
        "avg_tokens": round(avg_tokens, 2),
        "suitability_score": suitability_score,
    }


def benchmark_rows(
    *,
    data_dir: str,
    task_kind: str | None = None,
    top_n: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_task_kind = str(task_kind or "").strip().lower()
    if normalized_task_kind not in BENCH_TASK_KINDS:
        normalized_task_kind = ""
    db = load_benchmarks(data_dir)
    rows: list[dict[str, Any]] = []
    for key, entry in (db.get("models") or {}).items():
        if not isinstance(entry, dict):
            continue
        overall = score_bucket(entry.get("overall") or {})
        row = {
            "id": key,
            "provider": str(entry.get("provider") or "").strip().lower(),
            "model": str(entry.get("model") or "").strip(),
            "overall": overall,
            "task_kinds": {kind: score_bucket(((entry.get("task_kinds") or {}).get(kind) or {})) for kind in BENCH_TASK_KINDS},
        }
        row["focus"] = row["task_kinds"].get(normalized_task_kind, score_bucket({})) if normalized_task_kind else overall
        row["_sort_score"] = float((row["focus"] or {}).get("suitability_score") or 0.0)
        rows.append(row)
    rows.sort(key=lambda item: item.get("_sort_score") or 0.0, reverse=True)
    if isinstance(top_n, int) and top_n > 0:
        rows = rows[:top_n]
    for row in rows:
        row.pop("_sort_score", None)
    return rows, db


def timeseries_from_samples(samples: list[dict[str, Any]], bucket: str = "day") -> list[dict[str, Any]]:
    step = 86400 if bucket == "day" else 3600
    grouped: dict[int, dict[str, Any]] = {}
    for sample in samples:
        ts = int((sample or {}).get("ts") or 0)
        if ts <= 0:
            continue
        key = int(ts // step) * step
        row = grouped.setdefault(
            key,
            {"timestamp": key, "total": 0, "success": 0, "quality_pass": 0, "latency_ms_total": 0, "tokens_total": 0},
        )
        row["total"] += 1
        row["success"] += 1 if bool(sample.get("success")) else 0
        row["quality_pass"] += 1 if bool(sample.get("quality_passed")) else 0
        row["latency_ms_total"] += max(0, int(sample.get("latency_ms") or 0))
        row["tokens_total"] += max(0, int(sample.get("tokens_total") or 0))

    points: list[dict[str, Any]] = []
    for ts in sorted(grouped):
        row = grouped[ts]
        total = max(1, int(row["total"]))
        scored = score_bucket(
            {
                "total": row["total"],
                "success": row["success"],
                "quality_pass": row["quality_pass"],
                "latency_ms_total": row["latency_ms_total"],
                "tokens_total": row["tokens_total"],
            }
        )
        points.append({"timestamp": ts, **scored})
    return points


def resolve_benchmark_identity(proposal_meta: dict | None, agent_cfg: dict | None) -> tuple[str, str]:
    proposal_meta = proposal_meta or {}
    agent_cfg = agent_cfg or {}
    routing = proposal_meta.get("routing") or {}
    llm_cfg = agent_cfg.get("llm_config") or {}
    precedence = benchmark_identity_precedence_config(agent_cfg)
    provider_sources = {
        "proposal_backend": proposal_meta.get("backend"),
        "routing_effective_backend": routing.get("effective_backend"),
        "llm_config_provider": llm_cfg.get("provider"),
        "default_provider": agent_cfg.get("default_provider"),
        "provider": agent_cfg.get("provider"),
    }
    model_sources = {
        "proposal_model": proposal_meta.get("model"),
        "llm_config_model": llm_cfg.get("model"),
        "default_model": agent_cfg.get("default_model"),
        "model": agent_cfg.get("model"),
    }
    provider = ""
    for key in precedence["provider_order"]:
        value = str(provider_sources.get(key) or "").strip().lower()
        if value:
            provider = value
            break
    model = ""
    for key in precedence["model_order"]:
        value = str(model_sources.get(key) or "").strip()
        if value:
            model = value
            break
    return provider or "unknown", model or "unknown"
