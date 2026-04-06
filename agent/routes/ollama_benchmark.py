from __future__ import annotations

from flask import Blueprint, current_app, g, request

from agent.auth import admin_required, check_auth
from agent.common.audit import log_audit
from agent.common.errors import NotFoundError, api_response
from agent.ollama_benchmark import (
    OLLAMA_BENCH_TASK_KINDS,
    merge_ollama_bench_config,
    save_ollama_bench_config,
)
from agent.services.benchmark_job_service import get_benchmark_job_service
from agent.services.ollama_benchmark_service import get_ollama_benchmark_service

ollama_benchmark_bp = Blueprint("ollama_benchmark", __name__)


def _current_username() -> str:
    user = getattr(g, "user", {}) or {}
    return str(user.get("sub") or user.get("username") or "anonymous")


@ollama_benchmark_bp.route("/ollama/benchmark/config", methods=["GET"])
@check_auth
def get_ollama_benchmark_config():
    service = get_ollama_benchmark_service()
    cfg = service.get_config()
    return api_response(data=cfg)


@ollama_benchmark_bp.route("/ollama/benchmark/config", methods=["PUT", "PATCH"])
@admin_required
def update_ollama_benchmark_config():
    data = request.get_json(silent=True) or {}
    service = get_ollama_benchmark_service()
    current_cfg = service.get_config()
    updated_cfg = merge_ollama_bench_config(current_cfg, data)
    save_ollama_bench_config(service.data_dir, updated_cfg)
    log_audit("ollama_benchmark_config_updated", {"changes": list(data.keys())})
    return api_response(data=updated_cfg)


@ollama_benchmark_bp.route("/ollama/benchmark/models/discover", methods=["GET"])
@check_auth
def discover_ollama_models():
    service = get_ollama_benchmark_service()
    models = service.discover_available_models()
    return api_response(data={"count": len(models), "models": models})


@ollama_benchmark_bp.route("/ollama/benchmark/roles", methods=["GET"])
@check_auth
def get_role_templates():
    service = get_ollama_benchmark_service()
    roles = service.get_role_templates()
    role_names = service.get_role_names()
    return api_response(data={"roles": role_names, "templates": roles})


@ollama_benchmark_bp.route("/ollama/benchmark/results", methods=["GET"])
@check_auth
def get_ollama_benchmark_results():
    role_name = str(request.args.get("role_name") or "").strip().lower() or None
    model_name = str(request.args.get("model_name") or "").strip().lower() or None
    top_n = max(1, min(50, int(request.args.get("top_n") or 20)))
    service = get_ollama_benchmark_service()
    rows, db = service.get_results(role_name=role_name, model_name=model_name, top_n=top_n)
    return api_response(
        data={
            "role_name": role_name,
            "model_name": model_name,
            "updated_at": db.get("updated_at"),
            "last_benchmark_run": db.get("last_benchmark_run"),
            "total_models": len(db.get("models", {})),
            "items": rows,
        }
    )


@ollama_benchmark_bp.route("/ollama/benchmark/comparison", methods=["GET"])
@check_auth
def get_model_comparison():
    role_name = str(request.args.get("role_name") or "").strip().lower() or None
    task_kind = str(request.args.get("task_kind") or "").strip().lower() or None
    top_n = max(1, min(20, int(request.args.get("top_n") or 10)))
    service = get_ollama_benchmark_service()
    result = service.get_model_comparison(role_name=role_name, task_kind=task_kind, top_n=top_n)
    return api_response(data=result)


@ollama_benchmark_bp.route("/ollama/benchmark/recommend", methods=["GET"])
@check_auth
def get_ollama_recommendation():
    role_name = str(request.args.get("role_name") or "").strip().lower() or None
    task_kind = str(request.args.get("task_kind") or "").strip().lower() or None
    service = get_ollama_benchmark_service()
    result = service.get_recommendation(role_name=role_name, task_kind=task_kind)
    return api_response(data=result)


@ollama_benchmark_bp.route("/ollama/benchmark/run", methods=["POST"])
@admin_required
def run_ollama_benchmark():
    data = request.get_json(silent=True) or {}
    models = data.get("models")
    if models and isinstance(models, list):
        models = [str(m or "").strip() for m in models if str(m or "").strip()]
    roles = data.get("roles")
    if roles and isinstance(roles, list):
        roles = [str(r or "").strip().lower() for r in roles if str(r or "").strip()]
    parameter_variations = bool(data.get("parameter_variations", False))
    max_minutes = max(1, min(120, int(data.get("max_execution_minutes") or 60)))
    base_url = str(data.get("base_url") or "").strip() or None
    run_async = bool(data.get("run_async", True))
    if run_async:
        job = get_benchmark_job_service().submit_ollama_benchmark_job(
            models=models,
            roles=roles,
            parameter_variations=parameter_variations,
            max_execution_minutes=max_minutes,
            base_url=base_url,
            created_by=_current_username(),
        )
        log_audit(
            "ollama_benchmark_run_submitted",
            {
                "job_id": job.get("job_id"),
                "models": models,
                "roles": roles,
                "parameter_variations": parameter_variations,
            },
        )
        return api_response(status="accepted", code=202, data={"job": job})
    service = get_ollama_benchmark_service()
    result = service.run_full_benchmark(
        models=models,
        roles=roles,
        parameter_variations=parameter_variations,
        max_execution_minutes=max_minutes,
        base_url=base_url,
    )
    log_audit(
        "ollama_benchmark_run",
        {
            "status": result.get("status"),
            "models_tested": result.get("models_tested"),
            "roles_tested": result.get("roles_tested"),
            "parameter_variations": parameter_variations,
        },
    )
    return api_response(data=result)


@ollama_benchmark_bp.route("/ollama/benchmark/jobs/<job_id>", methods=["GET"])
@check_auth
def get_ollama_benchmark_job(job_id: str):
    job = get_benchmark_job_service().get_job(job_id)
    if job is None or job.get("job_type") != "ollama_benchmark":
        raise NotFoundError("benchmark_job_not_found")
    return api_response(data={"job": job})


@ollama_benchmark_bp.route("/ollama/benchmark/role", methods=["POST"])
@admin_required
def run_role_benchmark():
    data = request.get_json(silent=True) or {}
    model = str(data.get("model") or "").strip()
    role_name = str(data.get("role_name") or "").strip().lower()
    parameters = data.get("parameters")
    if parameters and isinstance(parameters, dict):
        parameters = {
            k: v for k, v in parameters.items() if k in {"temperature", "top_p", "top_k", "num_ctx", "repeat_penalty"}
        }
    if not model:
        return api_response(status="error", message="model_required", code=400)
    if not role_name:
        return api_response(status="error", message="role_name_required", code=400)
    base_url = str(data.get("base_url") or "").strip() or None
    timeout = max(30, min(300, int(data.get("timeout") or 120)))
    service = get_ollama_benchmark_service()
    results = service.run_role_benchmark(
        model=model,
        role_name=role_name,
        parameters=parameters,
        base_url=base_url,
        timeout=timeout,
    )
    log_audit("ollama_role_benchmark", {"model": model, "role_name": role_name, "results_count": len(results)})
    return api_response(data={"model": model, "role_name": role_name, "results": results})


@ollama_benchmark_bp.route("/ollama/benchmark/parameters", methods=["POST"])
@admin_required
def run_parameter_variation():
    data = request.get_json(silent=True) or {}
    model = str(data.get("model") or "").strip()
    role_name = str(data.get("role_name") or "").strip().lower()
    if not model:
        return api_response(status="error", message="model_required", code=400)
    if not role_name:
        return api_response(status="error", message="role_name_required", code=400)
    base_url = str(data.get("base_url") or "").strip() or None
    service = get_ollama_benchmark_service()
    results = service.run_parameter_variation_benchmark(
        model=model,
        role_name=role_name,
        base_url=base_url,
        timeout=120,
    )
    log_audit("ollama_parameter_variation", {"model": model, "role_name": role_name, "tests_count": len(results)})
    return api_response(data={"model": model, "role_name": role_name, "results": results})


@ollama_benchmark_bp.route("/ollama/benchmark/single", methods=["POST"])
@admin_required
def run_single_benchmark():
    data = request.get_json(silent=True) or {}
    model = str(data.get("model") or "").strip()
    role_name = str(data.get("role_name") or "").strip().lower()
    prompt = str(data.get("prompt") or "").strip()
    task_kind = str(data.get("task_kind") or "analysis").strip().lower()
    parameters = data.get("parameters")
    if parameters and isinstance(parameters, dict):
        parameters = {
            k: v for k, v in parameters.items() if k in {"temperature", "top_p", "top_k", "num_ctx", "repeat_penalty"}
        }
    if not model:
        return api_response(status="error", message="model_required", code=400)
    if not role_name:
        return api_response(status="error", message="role_name_required", code=400)
    if not prompt:
        return api_response(status="error", message="prompt_required", code=400)
    if task_kind not in OLLAMA_BENCH_TASK_KINDS:
        task_kind = "analysis"
    base_url = str(data.get("base_url") or "").strip() or None
    timeout = max(30, min(300, int(data.get("timeout") or 120)))
    service = get_ollama_benchmark_service()
    result = service.run_single_benchmark(
        model=model,
        role_name=role_name,
        task_kind=task_kind,
        prompt=prompt,
        parameters=parameters,
        base_url=base_url,
        timeout=timeout,
    )
    log_audit("ollama_single_benchmark", {"model": model, "role_name": role_name, "success": result.get("success")})
    return api_response(data=result)


@ollama_benchmark_bp.route("/ollama/benchmark/task-kinds", methods=["GET"])
@check_auth
def get_task_kinds():
    return api_response(data={"task_kinds": sorted(OLLAMA_BENCH_TASK_KINDS)})
