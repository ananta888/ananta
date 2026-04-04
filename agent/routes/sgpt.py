import logging
import time
import uuid
from pathlib import Path

from flask import Blueprint, current_app, g, request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.common.sgpt import (
    SUPPORTED_CLI_BACKENDS,
    get_cli_backend_capabilities,
    get_cli_backend_preflight,
    get_cli_backend_runtime_status,
    normalize_backend_flags,
    resolve_codex_runtime_config,
    run_llm_cli_command,
)
from agent.config import settings
from agent.metrics import RAG_CHUNKS_SELECTED, RAG_REQUESTS_TOTAL, RAG_RETRIEVAL_DURATION
from agent.models import SgptContextRequest, SgptExecuteRequest, SgptSourceRequest
from agent.models import SgptSessionCreateRequest, SgptSessionTurnRequest
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_backend import is_research_backend, normalize_research_artifact
from agent.runtime_policy import build_trace_record, normalize_task_kind, resolve_cli_backend, runtime_routing_config
from agent.services.cli_session_service import get_cli_session_service
from agent.services.service_registry import get_core_services
from agent.utils import validate_request

audit_logger = logging.getLogger("audit")

# Rate Limiting State
RATE_LIMIT_WINDOW = 60  # seconds
MAX_REQUESTS_PER_WINDOW = 5
user_requests = {}  # compatibility shim for older tests and callers

sgpt_bp = Blueprint("sgpt", __name__)


def _log():
    return get_core_services().log_service.bind(__name__)


def get_rag_service():
    return get_core_services().rag_service


def get_rate_limit_service():
    return get_core_services().rate_limit_service


ALLOWED_BACKENDS = {*SUPPORTED_CLI_BACKENDS, "auto"}

SOURCE_ALLOWED_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".log",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
}


def _cli_session_policy() -> dict:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    mode = cfg.get("cli_session_mode") if isinstance(cfg.get("cli_session_mode"), dict) else {}
    backends = [str(item or "").strip().lower() for item in list(mode.get("stateful_backends") or ["opencode", "codex"]) if str(item or "").strip()]
    return {
        "enabled": bool(mode.get("enabled", False)),
        "stateful_backends": backends,
        "max_turns_per_session": max(1, min(int(mode.get("max_turns_per_session") or 40), 200)),
        "max_sessions": max(1, min(int(mode.get("max_sessions") or 200), 2000)),
    }


def _build_cli_error_details(errors: str, backend_used: str) -> dict | None:
    msg = str(errors or "")
    lower = msg.lower()
    if "cannot truncate prompt with n_keep" in lower and "n_ctx" in lower:
        return {
            "type": "context_limit_mismatch",
            "backend": backend_used,
            "hint": (
                "Model context window is too small for prompt/tool preamble. "
                "Increase context_limit or choose a model with larger n_ctx."
            ),
        }
    return None

def is_rate_limited(user_id: str) -> bool:
    """Checks whether user exceeded rate limit."""
    allowed = get_rate_limit_service().allow_request(
        namespace="sgpt",
        subject=str(user_id),
        limit=MAX_REQUESTS_PER_WINDOW,
        window_seconds=RATE_LIMIT_WINDOW,
    )
    if allowed:
        user_requests[str(user_id)] = []
    return not allowed


SGPT_CIRCUIT_BREAKER = {"failures": 0, "last_failure": 0, "open": False}
SGPT_CB_THRESHOLD = 5
SGPT_CB_RECOVERY_TIME = 60


def _extract_user_id() -> str:
    user_id = request.remote_addr or "unknown"
    if hasattr(g, "user") and isinstance(g.user, dict):
        user_id = g.user.get("sub", g.user.get("user_id", user_id))
    elif hasattr(g, "auth_payload") and isinstance(g.auth_payload, dict):
        user_id = g.auth_payload.get("sub", user_id)
    return str(user_id)


def _resolve_source_path(source_path: str) -> Path:
    repo_root = Path(settings.rag_repo_root).resolve()
    requested = (repo_root / source_path).resolve()
    requested.relative_to(repo_root)
    if requested.suffix.lower() not in SOURCE_ALLOWED_EXTENSIONS:
        raise ValueError("Source file type is not allowed")
    return requested


@sgpt_bp.route("/execute", methods=["POST"])
@check_auth
@validate_request(SgptExecuteRequest)
def execute_sgpt():
    """
    Executes SGPT command.
    JSON payload: {"prompt": "...", "options": ["--shell"], "use_hybrid_context": false}
    """
    if SGPT_CIRCUIT_BREAKER["open"]:
        if time.time() - SGPT_CIRCUIT_BREAKER["last_failure"] > SGPT_CB_RECOVERY_TIME:
            _log().info("SGPT circuit breaker switching to half-open.")
            SGPT_CIRCUIT_BREAKER["open"] = False
            SGPT_CIRCUIT_BREAKER["failures"] = 0
        else:
            return api_response(
                status="error",
                message="SGPT service is temporarily unavailable (circuit breaker open).",
                code=503,
            )

    user_id = _extract_user_id()
    if is_rate_limited(user_id):
        _log().warning("Rate limit exceeded for user %s", user_id)
        return api_response(status="error", message="Rate limit exceeded. Please try again later.", code=429)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_response(status="error", message="Invalid JSON payload", code=400)

    prompt = data.get("prompt")
    options = data.get("options", [])
    use_hybrid_context = bool(data.get("use_hybrid_context", False))
    backend = str(data.get("backend") or settings.sgpt_execution_backend or "sgpt").strip().lower()
    model = data.get("model")
    task_kind = normalize_task_kind(data.get("task_kind"), prompt or "")

    if not prompt:
        return api_response(status="error", message="Missing prompt", code=400)
    if not isinstance(options, list):
        return api_response(status="error", message="Options must be a list", code=400)
    if backend not in ALLOWED_BACKENDS:
        return api_response(status="error", message=f"Invalid backend. Allowed: {sorted(ALLOWED_BACKENDS)}", code=400)
    if model is not None and not isinstance(model, str):
        return api_response(status="error", message="model must be a string", code=400)
    if not all(isinstance(opt, str) for opt in options):
        return api_response(status="error", message="options must contain only strings", code=400)

    routing_reason = ""
    effective_backend, routing_reason, routing_cfg = resolve_cli_backend(
        task_kind=task_kind,
        requested_backend=backend,
        supported_backends=SUPPORTED_CLI_BACKENDS,
        agent_cfg=current_app.config.get("AGENT_CONFIG", {}) or {},
        fallback_backend="sgpt",
    )
    safe_options, rejected = normalize_backend_flags(effective_backend, options)
    if rejected:
        return api_response(
            status="error",
            message=f"Unsupported options for backend '{effective_backend}': {rejected}",
            code=400,
        )
    if effective_backend == "sgpt" and "--no-interaction" not in safe_options:
        safe_options.append("--no-interaction")

    try:
        context_payload = None
        effective_prompt = prompt
        degraded = False
        grounding = {"score": 0.0, "chunk_count": 0, "engine_diversity": 0}
        pipeline = new_pipeline_trace(
            pipeline="sgpt_execute",
            task_kind=task_kind,
            policy_version=routing_cfg["policy_version"],
            metadata={"requested_backend": backend},
        )
        if use_hybrid_context:
            stage_started = time.time()
            if not settings.rag_enabled:
                return api_response(status="error", message="Hybrid context mode is disabled", code=400)
            RAG_REQUESTS_TOTAL.labels(mode="execute").inc()
            with RAG_RETRIEVAL_DURATION.time():
                context_payload, effective_prompt = get_rag_service().build_execution_context(prompt)
            chunk_count = len(context_payload.get("chunks", []))
            RAG_CHUNKS_SELECTED.observe(chunk_count)
            engines = {str((c or {}).get("engine") or "") for c in (context_payload.get("chunks") or [])}
            diversity = len([e for e in engines if e])
            score = min(1.0, (chunk_count / max(1, settings.rag_max_chunks)) * 0.7 + min(diversity, 3) / 3.0 * 0.3)
            grounding = {"score": round(score, 3), "chunk_count": chunk_count, "engine_diversity": diversity}
            if chunk_count == 0:
                degraded = True
            append_stage(
                pipeline,
                name="retrieve",
                status="ok" if chunk_count > 0 else "degraded",
                metadata={"chunk_count": chunk_count, "engine_diversity": diversity},
                started_at=stage_started,
            )
        else:
            append_stage(pipeline, name="retrieve", status="skipped", metadata={"use_hybrid_context": False})

        append_stage(
            pipeline,
            name="route",
            status="ok",
            metadata={"requested_backend": backend, "effective_backend": effective_backend, "reason": routing_reason},
        )

        stage_started = time.time()
        returncode, output, errors, backend_used = run_llm_cli_command(
            effective_prompt,
            safe_options,
            backend=effective_backend,
            model=model,
            routing_policy={
                "mode": "adaptive",
                "task_kind": task_kind,
                "policy_version": routing_cfg["policy_version"],
            },
        )
        append_stage(
            pipeline,
            name="execute",
            status="ok" if returncode == 0 or bool(output) else "error",
            metadata={"backend_used": backend_used, "returncode": returncode},
            started_at=stage_started,
        )
        if returncode != 0 and not output:
            _log().error("LLM CLI (%s) Return Code %s: %s", backend_used, returncode, errors)
            SGPT_CIRCUIT_BREAKER["failures"] += 1
            SGPT_CIRCUIT_BREAKER["last_failure"] = time.time()
            if SGPT_CIRCUIT_BREAKER["failures"] >= SGPT_CB_THRESHOLD:
                SGPT_CIRCUIT_BREAKER["open"] = True
                _log().error("SGPT CIRCUIT BREAKER OPEN")
            details = _build_cli_error_details(errors, backend_used)
            return api_response(
                status="error",
                message=errors or f"LLM CLI ({backend_used}) failed with exit code {returncode}",
                data={"diagnostics": details} if details else None,
                code=500,
            )

        SGPT_CIRCUIT_BREAKER["failures"] = 0
        SGPT_CIRCUIT_BREAKER["open"] = False
        safe_output = output or ""
        safe_errors = errors or ""
        audit_logger.info(
            f"SGPT Success: output_len={len(safe_output)}",
            extra={
                "extra_fields": {
                    "action": "sgpt_success",
                    "output_len": len(safe_output),
                    "error_len": len(safe_errors),
                }
            },
        )
        trace = build_trace_record(
            task_id=None,
            event_type="sgpt_execute",
            task_kind=task_kind,
            backend=backend_used,
            requested_backend=backend,
            routing_reason=routing_reason,
            policy_version=routing_cfg["policy_version"],
            metadata={"degraded": degraded, "context_used": context_payload is not None},
        )
        response_data = {
            "trace_id": trace["trace_id"],
            "trace": trace,
            "pipeline": {**pipeline, "trace_id": trace["trace_id"]},
            "output": safe_output,
            "errors": safe_errors,
            "backend": backend_used,
            "routing": {
                "policy_version": routing_cfg["policy_version"],
                "task_kind": task_kind,
                "requested_backend": backend,
                "effective_backend": effective_backend,
                "reason": routing_reason,
                "confidence": 0.9 if backend != "auto" else 0.75,
            },
            "fallback": {"degraded_mode": degraded, "reason": "no_context_chunks" if degraded else None},
            "grounding": grounding,
        }
        if is_research_backend(backend_used):
            response_data["research_artifact"] = normalize_research_artifact(
                safe_output,
                backend=backend_used,
                cli_result={"stderr_preview": safe_errors[:240], "returncode": returncode},
            )
        if context_payload is not None:
            response_data["context"] = {
                "strategy": context_payload.get("strategy", {}),
                "policy_version": context_payload.get("policy_version", "v1"),
                "chunk_count": len(context_payload.get("chunks", [])),
                "token_estimate": context_payload.get("token_estimate", 0),
            }
        return api_response(data=response_data)
    except Exception as e:
        _log().exception("Error executing SGPT")
        audit_logger.error(f"SGPT Error: {str(e)}", extra={"extra_fields": {"action": "sgpt_error", "error": str(e)}})
        return api_response(status="error", message=str(e), code=500)


@sgpt_bp.route("/backends", methods=["GET"])
@check_auth
def list_cli_backends():
    capabilities = get_cli_backend_capabilities()
    runtime = get_cli_backend_runtime_status()
    preflight = get_cli_backend_preflight()
    configured_backend = (settings.sgpt_execution_backend or "sgpt").strip().lower()
    codex_runtime = resolve_codex_runtime_config()
    default_provider = str((current_app.config.get("AGENT_CONFIG", {}) or {}).get("default_provider") or settings.default_provider or "").strip().lower() or None
    data = {
        "configured_backend": configured_backend,
        "cli_session_mode": _cli_session_policy(),
        "cli_session_runtime": get_cli_session_service().snapshot(),
        "routing_dimensions": {
            "inference_provider_default": default_provider,
            "execution_backend_default": configured_backend,
            "codex_runtime_target": {
                "target_provider": codex_runtime.get("target_provider"),
                "target_kind": codex_runtime.get("target_kind"),
                "target_provider_type": codex_runtime.get("target_provider_type"),
                "base_url": codex_runtime.get("base_url"),
                "remote_hub": bool(codex_runtime.get("remote_hub")),
                "instance_id": codex_runtime.get("instance_id"),
                "max_hops": codex_runtime.get("max_hops"),
                "diagnostics": list(codex_runtime.get("diagnostics") or []),
            },
        },
        "supported_backends": capabilities,
        "runtime": runtime,
        "preflight": preflight,
    }
    return api_response(data=data)


@sgpt_bp.route("/capability-matrix", methods=["GET"])
@check_auth
def capability_matrix():
    capabilities = get_cli_backend_capabilities()
    matrix = []
    for backend, info in (capabilities or {}).items():
        matrix.append(
            {
                "backend": backend,
                "available": bool(info.get("available")),
                "supports_model_selection": bool(info.get("supports_model_selection")),
                "risk_level": "high" if backend in {"codex", "aider", "opencode", "mistral_code"} else "medium",
                "task_fit": {
                    "coding": backend in {"codex", "aider", "opencode", "mistral_code"},
                    "analysis": backend in {"sgpt", "codex", "opencode"},
                    "doc": backend in {"sgpt", "codex", "opencode"},
                    "ops": backend in {"opencode", "sgpt", "codex"},
                },
                "allowed_flags": info.get("supported_options", []),
            }
        )
    return api_response(data={"items": matrix, "policy": "capability_matrix_v1"})


@sgpt_bp.route("/sessions", methods=["POST"])
@check_auth
@validate_request(SgptSessionCreateRequest)
def create_cli_session():
    policy = _cli_session_policy()
    if not policy["enabled"]:
        return api_response(status="error", message="cli_sessions_disabled", code=403)
    data = request.get_json(silent=True) or {}
    backend = str(data.get("backend") or settings.sgpt_execution_backend or "opencode").strip().lower()
    if backend == "auto":
        backend = "opencode"
    if backend not in SUPPORTED_CLI_BACKENDS:
        return api_response(status="error", message=f"Invalid backend. Allowed: {sorted(SUPPORTED_CLI_BACKENDS)}", code=400)
    if backend not in set(policy["stateful_backends"]):
        return api_response(status="error", message="backend_not_stateful_enabled", code=400)
    session = get_cli_session_service().create_session(
        backend=backend,
        model=data.get("model"),
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
        task_id=data.get("task_id"),
        conversation_id=data.get("conversation_id"),
    )
    get_cli_session_service().prune_sessions(max_sessions=policy["max_sessions"])
    return api_response(data={"session": session, "policy": policy}, code=201)


@sgpt_bp.route("/sessions", methods=["GET"])
@check_auth
def list_cli_sessions():
    include_history = str(request.args.get("include_history") or "").strip().lower() in {"1", "true", "yes"}
    backend = str(request.args.get("backend") or "").strip().lower() or None
    limit = int(request.args.get("limit") or 100)
    items = get_cli_session_service().list_sessions(backend=backend, include_history=include_history, limit=limit)
    return api_response(data={"items": items, "count": len(items), "runtime": get_cli_session_service().snapshot()})


@sgpt_bp.route("/sessions/<session_id>", methods=["GET"])
@check_auth
def get_cli_session(session_id: str):
    include_history = str(request.args.get("include_history") or "1").strip().lower() in {"1", "true", "yes"}
    payload = get_cli_session_service().get_session(session_id, include_history=include_history)
    if payload is None:
        return api_response(status="error", message="session_not_found", code=404)
    return api_response(data=payload)


@sgpt_bp.route("/sessions/<session_id>", methods=["DELETE"])
@check_auth
def close_cli_session(session_id: str):
    closed = get_cli_session_service().close_session(session_id)
    if closed is None:
        return api_response(status="error", message="session_not_found", code=404)
    return api_response(data={"status": "closed", "session": closed})


@sgpt_bp.route("/sessions/<session_id>/turn", methods=["POST"])
@check_auth
@validate_request(SgptSessionTurnRequest)
def run_cli_session_turn(session_id: str):
    session = get_cli_session_service().get_session(session_id, include_history=True)
    if session is None:
        return api_response(status="error", message="session_not_found", code=404)
    if str(session.get("status") or "").strip().lower() != "active":
        return api_response(status="error", message="session_closed", code=409)
    data = request.get_json(silent=True) or {}
    prompt = str(data.get("prompt") or "").strip()
    if not prompt:
        return api_response(status="error", message="Missing prompt", code=400)
    backend = str(session.get("backend") or "").strip().lower() or "opencode"
    options = data.get("options", [])
    if not isinstance(options, list) or not all(isinstance(opt, str) for opt in options):
        return api_response(status="error", message="options must contain only strings", code=400)
    safe_options, rejected = normalize_backend_flags(backend, options)
    if rejected:
        return api_response(
            status="error",
            message=f"Unsupported options for backend '{backend}': {rejected}",
            code=400,
        )
    if backend == "sgpt" and "--no-interaction" not in safe_options:
        safe_options.append("--no-interaction")
    policy = _cli_session_policy()
    effective_prompt = get_cli_session_service().build_prompt_with_history(
        session_id=session_id,
        prompt=prompt,
        max_turns=policy["max_turns_per_session"],
    ) or prompt
    task_kind = normalize_task_kind(data.get("task_kind"), prompt)
    rc, out, err, backend_used = run_llm_cli_command(
        effective_prompt,
        safe_options,
        backend=backend,
        model=data.get("model") or session.get("model"),
        routing_policy={"mode": "stateful_session", "task_kind": task_kind, "policy_version": "session-v1"},
    )
    if rc != 0 and not out:
        return api_response(status="error", message=err or f"backend '{backend_used}' failed with exit code {rc}", code=500)
    turn = get_cli_session_service().append_turn(
        session_id=session_id,
        prompt=prompt,
        output=out or "",
        model=data.get("model") or session.get("model"),
        metadata={"backend_used": backend_used, "returncode": rc, "stderr_preview": (err or "")[:240]},
    )
    updated = get_cli_session_service().get_session(session_id, include_history=False)
    return api_response(
        data={
            "output": out or "",
            "errors": err or "",
            "backend": backend_used,
            "session_id": session_id,
            "session_turn": turn,
            "session": updated,
            "routing": {
                "task_kind": task_kind,
                "requested_backend": backend,
                "effective_backend": backend_used,
                "reason": "stateful_cli_session",
                "session_mode": "stateful",
            },
        }
    )


@sgpt_bp.route("/context", methods=["POST"])
@check_auth
@validate_request(SgptContextRequest)
def get_context():
    if not settings.rag_enabled:
        return api_response(status="error", message="Hybrid context mode is disabled", code=400)

    user_id = _extract_user_id()
    if is_rate_limited(user_id):
        _log().warning("Rate limit exceeded for user %s", user_id)
        return api_response(status="error", message="Rate limit exceeded. Please try again later.", code=429)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_response(status="error", message="Invalid JSON payload", code=400)

    query = data.get("query")
    if not query or not isinstance(query, str):
        return api_response(status="error", message="Missing query", code=400)

    include_context_text = bool(data.get("include_context_text", True))
    try:
        RAG_REQUESTS_TOTAL.labels(mode="context").inc()
        with RAG_RETRIEVAL_DURATION.time():
            payload = get_rag_service().retrieve_context_bundle(query, include_context_text=include_context_text)
        RAG_CHUNKS_SELECTED.observe(len(payload.get("chunks", [])))
        return api_response(data=payload)
    except Exception as e:
        _log().exception("Error building hybrid context")
        return api_response(status="error", message=str(e), code=500)


@sgpt_bp.route("/source", methods=["POST"])
@check_auth
@validate_request(SgptSourceRequest)
def get_source_preview():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_response(status="error", message="Invalid JSON payload", code=400)

    source_path = data.get("source_path")
    if not source_path or not isinstance(source_path, str):
        return api_response(status="error", message="Missing source_path", code=400)

    max_chars = int(data.get("max_chars", 1600) or 1600)
    max_chars = max(200, min(max_chars, 8000))

    try:
        file_path = _resolve_source_path(source_path)
    except Exception as e:
        _log().warning("Rejected source preview path '%s': %s", source_path, e)
        return api_response(status="error", message="Invalid source_path", code=400)

    if not file_path.exists() or not file_path.is_file():
        return api_response(status="error", message="Source file not found", code=404)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        _log().exception("Failed reading source preview file '%s'", file_path)
        return api_response(status="error", message=str(e), code=500)

    snippet = content[:max_chars]
    line_count = snippet.count("\n") + 1 if snippet else 0
    return api_response(
        data={
            "source_path": source_path,
            "preview": snippet,
            "truncated": len(content) > len(snippet),
            "line_count": line_count,
        }
    )
