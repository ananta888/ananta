from __future__ import annotations

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.hub_benchmark import HUB_BENCH_TASK_KINDS, load_hub_benchmark_config
from agent.services.hub_benchmark_service import get_hub_benchmark_service

hub_benchmark_bp = Blueprint("hub_benchmark", __name__)


@hub_benchmark_bp.route("/hub/benchmark/config", methods=["GET"])
@check_auth
def get_hub_benchmark_config():
    service = get_hub_benchmark_service()
    cfg = service.get_config()
    return api_response(data=cfg)


@hub_benchmark_bp.route("/hub/benchmark/results", methods=["GET"])
@check_auth
def get_hub_benchmark_results():
    role_name = str(request.args.get("role_name") or "").strip().lower() or None
    top_n = max(1, min(50, int(request.args.get("top_n") or 20)))
    service = get_hub_benchmark_service()
    rows, db = service.get_results(role_name=role_name, top_n=top_n)
    return api_response(
        data={
            "role_name": role_name,
            "updated_at": db.get("updated_at"),
            "last_benchmark_run": db.get("last_benchmark_run"),
            "items": rows,
        }
    )


@hub_benchmark_bp.route("/hub/benchmark/recommend", methods=["GET"])
@check_auth
def get_hub_benchmark_recommendation():
    role_name = str(request.args.get("role_name") or "").strip().lower() or None
    task_kind = str(request.args.get("task_kind") or "").strip().lower() or None
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    hub_cfg = agent_cfg.get("hub_copilot", {}) or {}
    llm_cfg = agent_cfg.get("llm_config", {}) or {}
    current_provider = str(hub_cfg.get("provider") or llm_cfg.get("provider") or "").strip().lower() or None
    current_model = str(hub_cfg.get("model") or llm_cfg.get("model") or "").strip() or None
    service = get_hub_benchmark_service()
    result = service.get_recommendation(
        role_name=role_name,
        task_kind=task_kind,
        current_provider=current_provider,
        current_model=current_model,
    )
    return api_response(data=result)


@hub_benchmark_bp.route("/hub/benchmark/model-for-task", methods=["GET"])
@check_auth
def get_hub_model_for_task():
    task_kind = str(request.args.get("task_kind") or "").strip().lower() or "planning"
    if task_kind not in HUB_BENCH_TASK_KINDS:
        task_kind = "planning"
    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    hub_cfg = agent_cfg.get("hub_copilot", {}) or {}
    llm_cfg = agent_cfg.get("llm_config", {}) or {}
    current_provider = str(hub_cfg.get("provider") or llm_cfg.get("provider") or "").strip().lower() or None
    current_model = str(hub_cfg.get("model") or llm_cfg.get("model") or "").strip() or None
    service = get_hub_benchmark_service()
    result = service.get_hub_model_recommendation_for_task(
        task_kind=task_kind,
        current_provider=current_provider,
        current_model=current_model,
    )
    return api_response(data=result)


@hub_benchmark_bp.route("/hub/benchmark/run", methods=["POST"])
@admin_required
def run_hub_benchmark():
    data = request.get_json(silent=True) or {}
    roles = data.get("roles")
    if roles and isinstance(roles, list):
        roles = [str(r or "").strip().lower() for r in roles if str(r or "").strip()]
    providers = data.get("providers")
    if providers and isinstance(providers, list):
        providers = [str(p or "").strip().lower() for p in providers if str(p or "").strip()]
    max_minutes = max(1, min(60, int(data.get("max_execution_minutes") or 30)))
    service = get_hub_benchmark_service()
    result = service.run_full_benchmark(
        roles=roles,
        providers=providers,
        max_execution_minutes=max_minutes,
    )
    log_audit("hub_benchmark_run", {"status": result.get("status"), "total_tests": result.get("total_tests")})
    return api_response(data=result)


@hub_benchmark_bp.route("/hub/benchmark/single", methods=["POST"])
@admin_required
def run_single_hub_benchmark():
    data = request.get_json(silent=True) or {}
    provider = str(data.get("provider") or "").strip().lower()
    model = str(data.get("model") or "").strip()
    role_name = str(data.get("role_name") or "").strip().lower()
    task_kind = str(data.get("task_kind") or "analysis").strip().lower()
    prompt = str(data.get("prompt") or "").strip()
    if not provider or not model:
        return api_response(status="error", message="provider_and_model_required", code=400)
    if not role_name:
        return api_response(status="error", message="role_name_required", code=400)
    if not prompt:
        return api_response(status="error", message="prompt_required", code=400)
    if task_kind not in HUB_BENCH_TASK_KINDS:
        task_kind = "analysis"
    base_url = str(data.get("base_url") or "").strip() or None
    temperature = data.get("temperature")
    if temperature is not None:
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            temperature = None
    timeout = max(10, min(300, int(data.get("timeout") or 60)))
    service = get_hub_benchmark_service()
    result = service.run_single_benchmark(
        provider=provider,
        model=model,
        role_name=role_name,
        task_kind=task_kind,
        prompt=prompt,
        base_url=base_url,
        temperature=temperature,
        timeout=timeout,
    )
    log_audit(
        "hub_benchmark_single",
        {"provider": provider, "model": model, "role_name": role_name, "success": result.get("success")},
    )
    return api_response(data=result)


@hub_benchmark_bp.route("/hub/benchmark/auto-trigger-status", methods=["GET"])
@check_auth
def get_auto_trigger_status():
    service = get_hub_benchmark_service()
    needed, reason = service.should_auto_trigger()
    return api_response(data={"auto_trigger_needed": needed, "reason": reason})


@hub_benchmark_bp.route("/hub/benchmark/task-kinds", methods=["GET"])
@check_auth
def get_task_kinds():
    return api_response(data={"task_kinds": sorted(HUB_BENCH_TASK_KINDS)})
