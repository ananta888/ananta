from __future__ import annotations

import time

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.llm_benchmarks import load_benchmarks, record_benchmark_sample, timeseries_from_samples
from agent.llm_integration import _load_lmstudio_history

from . import shared

benchmarks_bp = Blueprint("config_benchmarks", __name__)


@benchmarks_bp.route("/llm/history", methods=["GET"])
@check_auth
def get_llm_history():
    return api_response(data=_load_lmstudio_history())


@benchmarks_bp.route("/llm/benchmarks/record", methods=["POST"])
@admin_required
def record_llm_benchmark():
    data = request.get_json(silent=True) or {}
    provider = str(data.get("provider") or "").strip().lower()
    model = str(data.get("model") or "").strip()
    task_kind = str(data.get("task_kind") or "analysis").strip().lower()
    if task_kind not in shared._BENCH_TASK_KINDS:
        task_kind = "analysis"
    if not provider or not model:
        return api_response(status="error", message="provider_and_model_required", code=400)

    success = bool(data.get("success", False))
    quality_passed = bool(data.get("quality_gate_passed", success))
    latency_ms = max(0, int(data.get("latency_ms") or 0))
    tokens_total = max(0, int(data.get("tokens_total") or 0))
    cost_units = float(data.get("cost_units") or 0.0)
    result = record_benchmark_sample(
        data_dir=current_app.config.get("DATA_DIR") or "data",
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        provider=provider,
        model=model,
        task_kind=task_kind,
        success=success,
        quality_gate_passed=quality_passed,
        latency_ms=latency_ms,
        tokens_total=tokens_total,
        cost_units=cost_units,
    )
    model_key = result.get("model_key")
    log_audit("llm_benchmark_recorded", {"model_key": model_key, "task_kind": task_kind, "success": success})
    return api_response(data={"recorded": True, "model_key": model_key, "task_kind": task_kind})


@benchmarks_bp.route("/llm/benchmarks", methods=["GET"])
@check_auth
def get_llm_benchmarks():
    task_kind = str(request.args.get("task_kind") or "").strip().lower()
    top_n = max(1, min(100, int(request.args.get("top_n") or 20)))
    rows, db = shared.benchmark_rows_for_task(task_kind=task_kind, top_n=top_n)
    return api_response(
        data={
            "task_kind": task_kind if task_kind in shared._BENCH_TASK_KINDS else None,
            "updated_at": db.get("updated_at"),
            "items": rows,
        }
    )


@benchmarks_bp.route("/llm/benchmarks/timeseries", methods=["GET"])
@check_auth
def get_llm_benchmarks_timeseries():
    provider = str(request.args.get("provider") or "").strip().lower()
    model = str(request.args.get("model") or "").strip()
    task_kind = str(request.args.get("task_kind") or "").strip().lower()
    bucket = str(request.args.get("bucket") or "day").strip().lower()
    if bucket not in {"day", "hour"}:
        bucket = "day"
    days = max(1, min(365, int(request.args.get("days") or 30)))
    retention = shared.benchmark_retention_settings()
    min_ts = int(time.time()) - (days * 86400)
    effective_min_ts = max(min_ts, int(time.time()) - (retention["max_days"] * 86400))
    db = load_benchmarks(current_app.config.get("DATA_DIR") or "data")
    items = []
    for key, entry in (db.get("models") or {}).items():
        if not isinstance(entry, dict):
            continue
        entry_provider = str(entry.get("provider") or "").strip().lower()
        entry_model = str(entry.get("model") or "").strip()
        if provider and entry_provider != provider:
            continue
        if model and entry_model != model:
            continue
        source_bucket = entry.get("overall") or {}
        if task_kind in shared._BENCH_TASK_KINDS:
            source_bucket = (entry.get("task_kinds") or {}).get(task_kind) or {}
        samples = [sample for sample in (source_bucket.get("samples") or []) if int((sample or {}).get("ts") or 0) >= effective_min_ts]
        items.append(
            {
                "id": key,
                "provider": entry_provider,
                "model": entry_model,
                "task_kind": task_kind if task_kind in shared._BENCH_TASK_KINDS else None,
                "bucket": bucket,
                "points": timeseries_from_samples(samples, bucket=bucket),
            }
        )
    return api_response(data={"updated_at": db.get("updated_at"), "days": days, "bucket": bucket, "retention": retention, "items": items})


@benchmarks_bp.route("/llm/benchmarks/config", methods=["GET"])
@check_auth
def get_llm_benchmarks_config():
    return api_response(
        data={
            "retention": shared.benchmark_retention_settings(),
            "identity_precedence": shared.benchmark_identity_precedence_settings(),
            "defaults": {
                "retention": shared._DEFAULT_BENCH_RETENTION,
                "identity_precedence": {
                    "provider_order": shared._DEFAULT_BENCH_PROVIDER_ORDER,
                    "model_order": shared._DEFAULT_BENCH_MODEL_ORDER,
                },
            },
        }
    )
