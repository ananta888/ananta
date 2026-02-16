import logging
import time
from pathlib import Path

from flask import Blueprint, g, request, current_app

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.common.sgpt import (
    SUPPORTED_CLI_BACKENDS,
    get_cli_backend_capabilities,
    get_cli_backend_runtime_status,
    normalize_backend_flags,
    run_llm_cli_command,
)
from agent.config import settings
from agent.hybrid_orchestrator import HybridOrchestrator
from agent.metrics import RAG_CHUNKS_SELECTED, RAG_REQUESTS_TOTAL, RAG_RETRIEVAL_DURATION
from agent.models import SgptContextRequest, SgptExecuteRequest, SgptSourceRequest
from agent.redis import get_redis_client
from agent.utils import validate_request

audit_logger = logging.getLogger("audit")

# Rate Limiting State
RATE_LIMIT_WINDOW = 60  # seconds
MAX_REQUESTS_PER_WINDOW = 5
user_requests = {}  # {user_id: [timestamps]} fallback for in-memory

sgpt_bp = Blueprint("sgpt", __name__)

ALLOWED_BACKENDS = {*SUPPORTED_CLI_BACKENDS, "auto"}

_orchestrator: HybridOrchestrator | None = None
_orchestrator_signature: tuple | None = None
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


def _orchestrator_config_signature() -> tuple:
    return (
        settings.rag_enabled,
        settings.rag_repo_root,
        settings.rag_data_roots,
        settings.rag_max_context_chars,
        settings.rag_max_context_tokens,
        settings.rag_max_chunks,
        settings.rag_agentic_max_commands,
        settings.rag_agentic_timeout_seconds,
        settings.rag_semantic_persist_dir,
        settings.rag_redact_sensitive,
    )


def get_orchestrator() -> HybridOrchestrator:
    global _orchestrator, _orchestrator_signature
    signature = _orchestrator_config_signature()
    if _orchestrator is not None and _orchestrator_signature == signature:
        return _orchestrator

    repo_root = Path(settings.rag_repo_root).resolve()
    data_roots = [repo_root / p.strip() for p in settings.rag_data_roots.split(",") if p.strip()]
    persist_dir = repo_root / settings.rag_semantic_persist_dir
    _orchestrator = HybridOrchestrator(
        repo_root=repo_root,
        data_roots=data_roots,
        max_context_chars=settings.rag_max_context_chars,
        max_context_tokens=settings.rag_max_context_tokens,
        max_chunks=settings.rag_max_chunks,
        agentic_max_commands=settings.rag_agentic_max_commands,
        agentic_timeout_seconds=settings.rag_agentic_timeout_seconds,
        semantic_persist_dir=persist_dir,
        redact_sensitive=settings.rag_redact_sensitive,
    )
    _orchestrator_signature = signature
    return _orchestrator


def is_rate_limited(user_id: str) -> bool:
    """Checks whether user exceeded rate limit."""
    now = time.time()
    redis_client = get_redis_client()

    if redis_client:
        try:
            key = f"rate_limit:sgpt:{user_id}"
            current = redis_client.get(key)
            if current and int(current) >= MAX_REQUESTS_PER_WINDOW:
                return True

            pipe = redis_client.pipeline()
            pipe.incr(key)
            pipe.expire(key, RATE_LIMIT_WINDOW)
            pipe.execute()
            return False
        except Exception as e:
            logging.error(f"Redis error in rate limiting: {e}. Falling back to in-memory.")

    if user_id not in user_requests:
        user_requests[user_id] = [now]
        return False

    user_requests[user_id] = [ts for ts in user_requests[user_id] if now - ts < RATE_LIMIT_WINDOW]
    if len(user_requests[user_id]) >= MAX_REQUESTS_PER_WINDOW:
        return True

    user_requests[user_id].append(now)
    return False


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


def _normalize_task_kind(task_kind: str | None, prompt: str) -> str:
    if task_kind:
        val = str(task_kind).strip().lower()
        if val in {"coding", "analysis", "doc", "ops"}:
            return val
    text = (prompt or "").lower()
    if any(k in text for k in ("refactor", "implement", "fix", "code", "test", "bug")):
        return "coding"
    if any(k in text for k in ("deploy", "docker", "restart", "kubernetes", "ops", "infrastructure")):
        return "ops"
    if any(k in text for k in ("readme", "documentation", "docs", "explain")):
        return "doc"
    return "analysis"


def _routing_config() -> dict:
    cfg = (current_app.config.get("AGENT_CONFIG", {}) or {}).get("sgpt_routing", {}) or {}
    return {
        "policy_version": str(cfg.get("policy_version") or "v2"),
        "default_backend": str(cfg.get("default_backend") or "sgpt").strip().lower(),
        "task_kind_backend": cfg.get("task_kind_backend") or {},
    }


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
            logging.info("SGPT circuit breaker switching to half-open.")
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
        logging.warning(f"Rate limit exceeded for user {user_id}")
        return api_response(status="error", message="Rate limit exceeded. Please try again later.", code=429)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_response(status="error", message="Invalid JSON payload", code=400)

    prompt = data.get("prompt")
    options = data.get("options", [])
    use_hybrid_context = bool(data.get("use_hybrid_context", False))
    backend = str(data.get("backend") or settings.sgpt_execution_backend or "sgpt").strip().lower()
    model = data.get("model")
    task_kind = _normalize_task_kind(data.get("task_kind"), prompt or "")

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
    routing_cfg = _routing_config()
    if backend == "auto":
        kind_map = routing_cfg.get("task_kind_backend") or {}
        mapped = str(kind_map.get(task_kind) or "").strip().lower()
        if mapped in SUPPORTED_CLI_BACKENDS:
            effective_backend = mapped
            routing_reason = f"task_kind_policy:{task_kind}->{mapped}"
        else:
            configured = str(routing_cfg.get("default_backend") or settings.sgpt_execution_backend or "sgpt").strip().lower()
            effective_backend = configured if configured in SUPPORTED_CLI_BACKENDS else "sgpt"
            routing_reason = f"default_policy:{effective_backend}"
    else:
        effective_backend = backend
        routing_reason = f"explicit_backend:{effective_backend}"
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
        if use_hybrid_context:
            if not settings.rag_enabled:
                return api_response(status="error", message="Hybrid context mode is disabled", code=400)
            RAG_REQUESTS_TOTAL.labels(mode="execute").inc()
            with RAG_RETRIEVAL_DURATION.time():
                context_payload = get_orchestrator().get_relevant_context(prompt)
            RAG_CHUNKS_SELECTED.observe(len(context_payload.get("chunks", [])))
            effective_prompt = (
                "Nutze den folgenden selektiven Kontext und beantworte die Frage praezise.\n\n"
                f"Frage:\n{prompt}\n\n"
                f"Kontext:\n{context_payload.get('context_text', '')}"
            )

        returncode, output, errors, backend_used = run_llm_cli_command(
            effective_prompt,
            safe_options,
            backend=effective_backend,
            model=model,
            routing_policy={"mode": "adaptive", "task_kind": task_kind, "policy_version": routing_cfg["policy_version"]},
        )
        if returncode != 0 and not output:
            logging.error(f"LLM CLI ({backend_used}) Return Code {returncode}: {errors}")
            SGPT_CIRCUIT_BREAKER["failures"] += 1
            SGPT_CIRCUIT_BREAKER["last_failure"] = time.time()
            if SGPT_CIRCUIT_BREAKER["failures"] >= SGPT_CB_THRESHOLD:
                SGPT_CIRCUIT_BREAKER["open"] = True
                logging.error("SGPT CIRCUIT BREAKER OPEN")
            return api_response(
                status="error",
                message=errors or f"LLM CLI ({backend_used}) failed with exit code {returncode}",
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
        response_data = {
            "output": safe_output,
            "errors": safe_errors,
            "backend": backend_used,
            "routing": {
                "policy_version": routing_cfg["policy_version"],
                "task_kind": task_kind,
                "requested_backend": backend,
                "effective_backend": effective_backend,
                "reason": routing_reason,
            },
        }
        if context_payload is not None:
            response_data["context"] = {
                "strategy": context_payload.get("strategy", {}),
                "policy_version": context_payload.get("policy_version", "v1"),
                "chunk_count": len(context_payload.get("chunks", [])),
                "token_estimate": context_payload.get("token_estimate", 0),
            }
        return api_response(data=response_data)
    except Exception as e:
        logging.exception("Error executing SGPT")
        audit_logger.error(f"SGPT Error: {str(e)}", extra={"extra_fields": {"action": "sgpt_error", "error": str(e)}})
        return api_response(status="error", message=str(e), code=500)


@sgpt_bp.route("/backends", methods=["GET"])
@check_auth
def list_cli_backends():
    capabilities = get_cli_backend_capabilities()
    runtime = get_cli_backend_runtime_status()
    configured_backend = (settings.sgpt_execution_backend or "sgpt").strip().lower()
    data = {"configured_backend": configured_backend, "supported_backends": capabilities, "runtime": runtime}
    return api_response(data=data)


@sgpt_bp.route("/context", methods=["POST"])
@check_auth
@validate_request(SgptContextRequest)
def get_context():
    if not settings.rag_enabled:
        return api_response(status="error", message="Hybrid context mode is disabled", code=400)

    user_id = _extract_user_id()
    if is_rate_limited(user_id):
        logging.warning(f"Rate limit exceeded for user {user_id}")
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
            payload = get_orchestrator().get_relevant_context(query)
        RAG_CHUNKS_SELECTED.observe(len(payload.get("chunks", [])))
        if not include_context_text:
            payload = {k: v for k, v in payload.items() if k != "context_text"}
        return api_response(data=payload)
    except Exception as e:
        logging.exception("Error building hybrid context")
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
        logging.warning(f"Rejected source preview path '{source_path}': {e}")
        return api_response(status="error", message="Invalid source_path", code=400)

    if not file_path.exists() or not file_path.is_file():
        return api_response(status="error", message="Source file not found", code=404)

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logging.exception(f"Failed reading source preview file '{file_path}'")
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
