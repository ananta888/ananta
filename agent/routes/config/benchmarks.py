import logging
import os
from flask import Blueprint, current_app, request
from agent.auth import check_auth
from agent.common.errors import api_response
from agent.llm_benchmarks import (
    load_benchmarks,
    save_benchmarks,
    record_benchmark_sample,
    timeseries_from_samples,
    benchmark_rows,
    benchmark_retention_config,
    benchmark_identity_precedence_config
)

benchmarks_bp = Blueprint("config_benchmarks", __name__)

@benchmarks_bp.route("/llm/benchmarks/record", methods=["POST"])
def record_llm_benchmark():
    check_auth()
    data = request.get_json(silent=True) or {}
    provider = data.get("provider")
    model = data.get("model")
    task_kind = data.get("task_kind")
    success = data.get("success", True)
    quality_gate_passed = data.get("quality_gate_passed", True)
    latency_ms = data.get("latency_ms", 0)
    tokens_total = data.get("tokens_total", 0)

    if not provider or not model:
        return api_response(status="error", message="provider und model erforderlich", code=400)

    record_benchmark_sample(
        data_dir=current_app.config.get("DATA_DIR") or "data",
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        provider=provider,
        model=model,
        task_kind=task_kind or "unknown",
        success=success,
        quality_gate_passed=quality_gate_passed,
        latency_ms=latency_ms,
        tokens_total=tokens_total
    )
    return api_response(status="success", message="Benchmark aufgezeichnet")

@benchmarks_bp.route("/llm/benchmarks", methods=["GET"])
def get_llm_benchmarks():
    check_auth()
    db = load_benchmarks(current_app.config.get("DATA_DIR") or "data")
    rows = benchmark_rows(db)
    return api_response(data={"benchmarks": rows})

@benchmarks_bp.route("/llm/benchmarks/timeseries", methods=["GET"])
def get_llm_benchmarks_timeseries():
    check_auth()
    db = load_benchmarks(current_app.config.get("DATA_DIR") or "data")
    provider = request.args.get("provider")
    model = request.args.get("model")
    task_kind = request.args.get("task_kind")
    days = int(request.args.get("days") or 30)

    ts_data = timeseries_from_samples(db, provider, model, task_kind, days)
    return api_response(data={"timeseries": ts_data})

@benchmarks_bp.route("/llm/benchmarks/config", methods=["GET"])
def get_llm_benchmarks_config():
    check_auth()
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    return api_response(data={
        "retention": benchmark_retention_config(agent_cfg),
        "precedence": benchmark_identity_precedence_config(agent_cfg)
    })
