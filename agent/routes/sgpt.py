import logging
import time
import uuid
from pathlib import Path

from flask import Blueprint, current_app, request

from agent.auth import admin_required, check_auth, resolve_configured_agent_token
from agent.cli_backends.sgpt import (
    SUPPORTED_CLI_BACKENDS,
    get_cli_backend_capabilities,
    normalize_backend_flags,
    resolve_codex_runtime_config,
    run_llm_cli_command,
)
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.common.gateways.worker_gateway import get_worker_gateway
from agent.config import settings
from agent.metrics import RAG_CHUNKS_SELECTED, RAG_REQUESTS_TOTAL, RAG_RETRIEVAL_DURATION
from agent.models import (
    SgptContextRequest,
    SgptExecuteRequest,
    SgptSessionCreateRequest,
    SgptSessionTurnRequest,
    SgptSourceRequest,
)
from agent.routes import sgpt_execute as _sgpt_execute
from agent.runtime_policy import (
    build_trace_record,
    normalize_task_kind,
    resolve_cli_backend,
    resolve_lora_adapter_routing,
    runtime_routing_config,
)
from agent.services.cli_session_service import get_cli_session_service
from agent.services.context_manager_service import get_context_manager_service as _get_context_manager_service
from agent.services.ml_intern_adapter_service import get_ml_intern_adapter_service
from agent.services.ml_intern_lora_inference_service import get_lora_inference_service
from agent.services.repository_registry import get_repository_registry
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


def get_context_manager_service():
    return _get_context_manager_service()


def get_rate_limit_service():
    return get_core_services().rate_limit_service


ALLOWED_BACKENDS = _sgpt_execute.ALLOWED_BACKENDS
BACKEND_ALIASES = _sgpt_execute.BACKEND_ALIASES

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


def _allowed_backends() -> set[str]:
    return _sgpt_execute.allowed_backends(ALLOWED_BACKENDS)


def _normalize_backend_name(value: str | None, *, default: str = "ananta-worker") -> str:
    return _sgpt_execute.normalize_backend_name(
        value,
        default=default,
        aliases=BACKEND_ALIASES,
    )


def _cli_session_policy() -> dict:
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    mode = cfg.get("cli_session_mode") if isinstance(cfg.get("cli_session_mode"), dict) else {}
    backends = [
        str(item or "").strip().lower()
        for item in list(mode.get("stateful_backends") or ["opencode", "codex"])
        if str(item or "").strip()
    ]
    return {
        "enabled": bool(mode.get("enabled", False)),
        "stateful_backends": backends,
        "max_turns_per_session": max(1, min(int(mode.get("max_turns_per_session") or 40), 200)),
        "max_sessions": max(1, min(int(mode.get("max_sessions") or 200), 2000)),
        "reuse_scope": str(mode.get("reuse_scope") or "task").strip().lower() or "task",
        "native_opencode_sessions": bool(mode.get("native_opencode_sessions", False)),
    }


def _has_native_opencode_runtime(session: dict | None) -> bool:
    metadata = (session or {}).get("metadata") if isinstance((session or {}).get("metadata"), dict) else {}
    runtime_meta = metadata.get("opencode_runtime") if isinstance(metadata.get("opencode_runtime"), dict) else {}
    return str(runtime_meta.get("kind") or "").strip().lower() == "native_server"


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


_extract_user_id = _sgpt_execute.extract_user_id
_parse_source_types = _sgpt_execute.parse_source_types


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
    """Execute SGPT through the focused route policy."""

    return _sgpt_execute.execute_sgpt_request(
        _sgpt_execute.SgptExecuteRuntime(
            settings=settings,
            policy=_sgpt_execute.SgptExecutePolicy(
                supported_backends=SUPPORTED_CLI_BACKENDS,
                allowed_backends=_allowed_backends,
                normalize_backend_name=_normalize_backend_name,
                extract_user_id=_extract_user_id,
                parse_source_types=_parse_source_types,
                normalize_task_kind=normalize_task_kind,
                runtime_routing_config=runtime_routing_config,
                resolve_cli_backend=resolve_cli_backend,
                normalize_backend_flags=normalize_backend_flags,
                resolve_lora_adapter_routing=resolve_lora_adapter_routing,
                build_trace_record=build_trace_record,
            ),
            circuit_breaker=SGPT_CIRCUIT_BREAKER,
            cb_threshold=SGPT_CB_THRESHOLD,
            cb_recovery_time=SGPT_CB_RECOVERY_TIME,
            is_rate_limited=is_rate_limited,
            get_context_manager_service=get_context_manager_service,
            get_lora_inference_service=get_lora_inference_service,
            get_ml_intern_adapter_service=get_ml_intern_adapter_service,
            run_llm_cli_command=run_llm_cli_command,
            get_logger=_log,
            audit_logger=audit_logger,
        )
    )


@sgpt_bp.route("/backends", methods=["GET"])
@check_auth
def list_cli_backends():
    registry_payload = get_core_services().integration_registry_service.list_execution_backends(include_preflight=True)
    capabilities = registry_payload.get("capabilities") or {}
    runtime = registry_payload.get("runtime") or {}
    preflight = registry_payload.get("preflight") or {}
    configured_backend = _normalize_backend_name(settings.sgpt_execution_backend, default="ananta-worker")
    codex_runtime = resolve_codex_runtime_config()
    from agent.cli_backends.opencode import resolve_claude_runtime_config

    claude_runtime = resolve_claude_runtime_config()
    default_provider = (
        str(
            (current_app.config.get("AGENT_CONFIG", {}) or {}).get("default_provider")
            or settings.default_provider
            or ""
        )
        .strip()
        .lower()
        or None
    )
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
                # CCA-003: Auth-Zustand fuer die UI — kein Secret,
                # nur Modus + Hinweis-Kommando.
                "auth_mode": codex_runtime.get("auth_mode", "api_key"),
                "api_key_required": bool(codex_runtime.get("api_key_required", True)),
            },
            "claude_runtime_target": {
                "enabled": bool(claude_runtime.get("enabled")),
                "auth_mode": claude_runtime.get("auth_mode", "claude_login"),
                "api_key_required": bool(claude_runtime.get("api_key_required", False)),
                "default_model": claude_runtime.get("default_model"),
                "permission_mode": claude_runtime.get("permission_mode"),
                "diagnostics": list(claude_runtime.get("diagnostics") or []),
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
                "risk_level": "high"
                if backend in {"codex", "claude_code", "aider", "opencode", "mistral_code"}
                else "medium",
                "task_fit": {
                    "coding": backend
                    in {"ananta-worker", "sgpt", "codex", "claude_code", "aider", "opencode", "mistral_code"},
                    "analysis": backend in {"ananta-worker", "sgpt", "codex", "claude_code", "opencode"},
                    "doc": backend in {"ananta-worker", "sgpt", "codex", "claude_code", "opencode"},
                    "ops": backend in {"ananta-worker", "opencode", "sgpt", "codex"},
                },
                "allowed_flags": info.get("supported_options", []),
            }
        )
    return api_response(data={"items": matrix, "policy": "capability_matrix_v1"})


def _normalized_supported_backend_or_none(backend_id: str) -> str | None:
    backend = str(backend_id or "").strip().lower()
    return backend if backend in SUPPORTED_CLI_BACKENDS else None


@sgpt_bp.route("/backends/<backend_id>/health", methods=["GET"])
@check_auth
def cli_backend_health(backend_id: str):
    """COMMON-003: Health-Status eines einzelnen CLI-Backends.

    Aggregiert Preflight- und Runtime-Sicht ohne neue Probe und ohne
    Secrets in der Antwort.
    """
    backend = _normalized_supported_backend_or_none(backend_id)
    if backend is None:
        return api_response(
            status="error", message=f"Unknown backend. Allowed: {sorted(SUPPORTED_CLI_BACKENDS)}", code=404
        )
    from agent.cli_backends.routing import get_cli_backend_preflight, get_cli_backend_runtime_status

    preflight = get_cli_backend_preflight(runtime_scope="worker")
    runtime = get_cli_backend_runtime_status().get(backend) or {}
    backend_preflight = (preflight.get("cli_backends") or {}).get(backend) or {}
    providers = preflight.get("providers") or {}
    provider_view = None
    if backend == "codex":
        provider_view = providers.get("codex")
    elif backend == "claude_code":
        provider_view = providers.get("claude")

    status = "ready"
    if not backend_preflight.get("binary_available"):
        status = "not_installed"
    elif backend == "claude_code" and not (provider_view or {}).get("enabled"):
        status = "disabled"
    return api_response(
        data={
            "backend": backend,
            "status": status,
            "preflight": backend_preflight,
            "provider": provider_view,
            "runtime": {k: v for k, v in runtime.items() if k not in {"api_key_source"}},
        }
    )


@sgpt_bp.route("/backends/<backend_id>/diagnose", methods=["POST"])
@check_auth
def cli_backend_diagnose(backend_id: str):
    """COMMON-003: Verify-Command-Diagnose (z.B. ``claude --version``)."""
    backend = _normalized_supported_backend_or_none(backend_id)
    if backend is None:
        return api_response(
            status="error", message=f"Unknown backend. Allowed: {sorted(SUPPORTED_CLI_BACKENDS)}", code=404
        )
    from agent.cli_backends.routing import diagnose_cli_backend

    result = diagnose_cli_backend(backend)
    audit_logger.info(
        f"CLI backend diagnose: {backend} -> {result.get('status')}",
        extra={"extra_fields": {"action": "cli_backend_diagnose", "backend": backend, "status": result.get("status")}},
    )
    return api_response(data=result)


@sgpt_bp.route("/backends/<backend_id>/test-run", methods=["POST"])
@check_auth
def cli_backend_test_run(backend_id: str):
    """COMMON-003: read-only Test-Run ueber den regulaeren Run-Pfad.

    Nutzt einen harmlosen Prompt; es wird nichts am Projekt geaendert
    (claude laeuft mit permission_mode=plan, codex via exec-Pfad).
    """
    backend = _normalized_supported_backend_or_none(backend_id)
    if backend is None:
        return api_response(
            status="error", message=f"Unknown backend. Allowed: {sorted(SUPPORTED_CLI_BACKENDS)}", code=404
        )
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "Antworte nur mit dem Wort: OK").strip()[:2000]
    model = str(body.get("model") or "").strip() or None
    try:
        timeout = int(body.get("timeout") or 120)
    except (TypeError, ValueError):
        timeout = 120
    timeout = max(10, min(timeout, 300))

    started = time.time()
    rc, out, err, backend_used = run_llm_cli_command(
        prompt=prompt,
        options=[],
        timeout=timeout,
        backend=backend,
        model=model,
        routing_policy={"allowed_backends": [backend]},
    )
    duration_ms = int((time.time() - started) * 1000)
    audit_logger.info(
        f"CLI backend test-run: {backend} rc={rc}",
        extra={
            "extra_fields": {"action": "cli_backend_test_run", "backend": backend, "rc": rc, "duration_ms": duration_ms}
        },
    )
    return api_response(
        data={
            "backend": backend,
            "backend_used": backend_used,
            "rc": rc,
            "ok": rc == 0,
            "stdout": (out or "")[:4000],
            "stderr": (err or "")[:4000],
            "duration_ms": duration_ms,
        }
    )


def _registered_worker(worker_url: str = "", *, worker_name: str = ""):
    normalized_url = str(worker_url or "").strip().rstrip("/")
    normalized_name = str(worker_name or "").strip().lower()
    if not normalized_url and not normalized_name:
        return None
    for agent in get_repository_registry().agent_repo.get_all() or ():
        identity_matches = (bool(normalized_url) and str(agent.url or "").strip().rstrip("/") == normalized_url) or (
            bool(normalized_name) and str(agent.name or "").strip().lower() == normalized_name
        )
        if (
            identity_matches
            and str(agent.role or "").strip().lower() == "worker"
            and bool(agent.registration_validated)
        ):
            return agent
    return None


_WORKER_CLI_CONFIG_FIELDS = {
    "codex": frozenset(
        {
            "auth_mode",
            "base_url",
            "prefer_lmstudio",
            "target_provider",
            "sandbox_mode",
        }
    ),
    "claude_code": frozenset(
        {
            "enabled",
            "auth_mode",
            "permission_mode",
            "default_model",
            "timeout_seconds",
        }
    ),
}


def _worker_cli_config_payload(backend: str, *, action: str) -> dict:
    config_key = "codex_cli" if backend == "codex" else "claude_cli"
    agent_config = current_app.config.get("AGENT_CONFIG", {}) or {}
    source = agent_config.get(config_key) or {}
    if not isinstance(source, dict):
        source = {}
    allowed_fields = _WORKER_CLI_CONFIG_FIELDS[backend]
    filtered = {key: value for key, value in source.items() if key in allowed_fields}
    if action == "login_start":
        filtered["auth_mode"] = "chatgpt_login" if backend == "codex" else "claude_login"
    return {config_key: filtered}


@sgpt_bp.route("/backends/<backend_id>/provision", methods=["POST"])
@admin_required
def cli_backend_provision(backend_id: str):
    """Provision a pinned, allowlisted CLI package on one registered Worker."""

    from agent.cli_backends.provisioning import (
        PROVISIONABLE_CLI_BACKENDS,
        CliBackendProvisioningError,
        get_cli_backend_provisioner,
    )

    backend = str(backend_id or "").strip().lower()
    if backend not in PROVISIONABLE_CLI_BACKENDS:
        return api_response(
            status="error",
            message="backend_not_provisionable",
            data={"allowed_backends": sorted(PROVISIONABLE_CLI_BACKENDS)},
            code=404,
        )

    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "status").strip().lower()
    if action not in {"status", "install"}:
        return api_response(status="error", message="invalid_provisioning_action", code=400)

    if settings.role == "hub":
        worker = _registered_worker(str(body.get("worker_url") or ""))
        if worker is None:
            return api_response(status="error", message="registered_worker_required", code=404)
        token = str(worker.token or "").strip() or resolve_configured_agent_token(current_app.config)
        if not token:
            return api_response(
                status="error",
                message="worker_service_token_unavailable",
                code=503,
            )
        result = get_worker_gateway().forward_task(
            str(worker.url),
            f"/api/sgpt/backends/{backend}/provision",
            {"action": action},
            token=token,
            timeout=620 if action == "install" else 60,
        )
        if not isinstance(result, dict) or result.get("status") == "error":
            return api_response(
                status="error",
                message="worker_provisioning_failed",
                data={
                    "worker": {"name": worker.name, "url": worker.url},
                    "worker_response": result,
                },
                code=502,
            )
        data = result.get("data") if isinstance(result.get("data"), dict) else result
        return api_response(
            data={
                **dict(data),
                "worker": {"name": worker.name, "url": worker.url},
            }
        )

    if settings.role != "worker":
        return api_response(status="error", message="worker_role_required", code=409)

    provisioner = get_cli_backend_provisioner()
    try:
        result = provisioner.install(backend) if action == "install" else provisioner.status(backend)
    except CliBackendProvisioningError as exc:
        reason_code = str(exc)
        if reason_code not in {
            "npm_not_available",
            "npm_install_failed",
            "installed_binary_verification_failed",
            "backend_not_provisionable",
            "TimeoutExpired",
        }:
            reason_code = "cli_backend_provisioning_internal_error"
        log_audit(
            "cli_backend_provisioning_failed",
            {"backend": backend, "action": action, "reason_code": reason_code},
        )
        return api_response(
            status="error",
            message="cli_backend_provisioning_failed",
            data={"backend": backend, "action": action, "reason_code": reason_code},
            code=500,
        )

    log_audit(
        "cli_backend_provisioned" if action == "install" else "cli_backend_provisioning_inspected",
        {
            "backend": backend,
            "action": action,
            "version": result.get("version"),
            "status": result.get("status"),
        },
    )
    return api_response(data={**result, "action": action})


@sgpt_bp.route("/backends/<backend_id>/worker-action", methods=["POST"])
@admin_required
def cli_backend_worker_action(backend_id: str):
    """Run a bounded CLI management action on one registered Worker."""

    backend = str(backend_id or "").strip().lower()
    if backend not in {"codex", "claude_code"}:
        return api_response(status="error", message="backend_not_provisionable", code=404)
    if settings.role != "hub":
        return api_response(status="error", message="hub_role_required", code=409)

    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip().lower()
    worker = _registered_worker(worker_name=str(body.get("worker_name") or ""))
    if worker is None:
        return api_response(status="error", message="registered_worker_required", code=404)

    if action == "diagnose":
        endpoint = f"/api/sgpt/backends/{backend}/diagnose"
        forwarded_body = {}
        timeout = 60
    elif action == "test_run":
        try:
            requested_timeout = int(body.get("timeout") or 120)
        except (TypeError, ValueError):
            requested_timeout = 120
        endpoint = f"/api/sgpt/backends/{backend}/test-run"
        forwarded_body = {
            "prompt": str(body.get("prompt") or "Antworte nur mit dem Wort: OK")[:2000],
            "model": str(body.get("model") or "")[:200] or None,
            "timeout": max(10, min(requested_timeout, 300)),
        }
        timeout = 320
    elif action in {
        "account_status",
        "login_start",
        "login_status",
        "login_input",
        "login_cancel",
    }:
        endpoint = f"/api/sgpt/backends/{backend}/account-login"
        forwarded_body = {"action": action}
        if action in {"login_status", "login_input", "login_cancel"}:
            forwarded_body["session_id"] = str(body.get("session_id") or "")[:200]
        if action == "login_input":
            forwarded_body["value"] = str(body.get("value") or "")[:4096]
        timeout = 65
    else:
        return api_response(status="error", message="invalid_worker_action", code=400)

    token = str(worker.token or "").strip() or resolve_configured_agent_token(current_app.config)
    if not token:
        return api_response(
            status="error",
            message="worker_service_token_unavailable",
            code=503,
        )
    if action in {"login_start", "test_run"}:
        config_result = get_worker_gateway().forward_task(
            str(worker.url),
            "/config",
            _worker_cli_config_payload(backend, action=action),
            token=token,
            timeout=60,
        )
        if not isinstance(config_result, dict) or config_result.get("status") == "error":
            return api_response(
                status="error",
                message="worker_cli_auth_config_failed",
                data={"worker": {"name": worker.name, "url": worker.url}},
                code=502,
            )
    result = get_worker_gateway().forward_task(
        str(worker.url),
        endpoint,
        forwarded_body,
        token=token,
        timeout=timeout,
    )
    if not isinstance(result, dict) or result.get("status") == "error":
        return api_response(
            status="error",
            message="worker_cli_action_failed",
            data={
                "worker": {"name": worker.name, "url": worker.url},
                "worker_response": result,
            },
            code=502,
        )
    data = result.get("data") if isinstance(result.get("data"), dict) else result
    return api_response(
        data={
            **dict(data),
            "worker": {"name": worker.name, "url": worker.url},
        }
    )


@sgpt_bp.route("/backends/<backend_id>/account-login", methods=["POST"])
@admin_required
def cli_backend_account_login(backend_id: str):
    """Manage a browser-assisted account login inside one Worker."""

    from agent.cli_backends.account_login import (
        SUPPORTED_ACCOUNT_LOGIN_BACKENDS,
        CliBackendAccountLoginError,
        get_cli_backend_account_login_service,
    )

    backend = str(backend_id or "").strip().lower()
    if backend not in SUPPORTED_ACCOUNT_LOGIN_BACKENDS:
        return api_response(status="error", message="account_login_backend_unsupported", code=404)
    if settings.role != "worker":
        return api_response(status="error", message="worker_role_required", code=409)

    body = request.get_json(silent=True) or {}
    action = str(body.get("action") or "").strip().lower()
    service = get_cli_backend_account_login_service()
    try:
        if action == "account_status":
            result = service.account_status(backend)
        elif action == "login_start":
            result = service.start(backend)
        elif action == "login_status":
            result = service.status(backend, str(body.get("session_id") or ""))
        elif action == "login_input":
            result = service.submit_input(
                backend,
                str(body.get("session_id") or ""),
                str(body.get("value") or ""),
            )
        elif action == "login_cancel":
            result = service.cancel(backend, str(body.get("session_id") or ""))
        else:
            return api_response(status="error", message="invalid_account_login_action", code=400)
    except CliBackendAccountLoginError as exc:
        reason_code = str(exc)
        response_code = 404 if reason_code in {"backend_not_installed", "account_login_session_not_found"} else 400
        log_audit(
            "cli_backend_account_login_failed",
            {"backend": backend, "action": action, "reason_code": reason_code},
        )
        return api_response(
            status="error",
            message=reason_code,
            data={"backend": backend, "action": action},
            code=response_code,
        )

    log_audit(
        "cli_backend_account_login_action",
        {
            "backend": backend,
            "action": action,
            "login_status": result.get("status"),
        },
    )
    return api_response(data=result)


@sgpt_bp.route("/backends/claude_code/write-armed-run", methods=["POST"])
@check_auth
def claude_write_armed_run():
    """write_armed-Run fuer Claude Code: schreibt nur in eine isolierte
    Workspace-Kopie und liefert den Diff als Artefakt
    (status=awaiting_diff_review). Der Diff wird nie automatisch
    angewendet — Uebernahme ist eine manuelle Review-Entscheidung.
    """
    body = request.get_json(silent=True) or {}
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        return api_response(status="error", message="prompt is required", code=400)
    workdir = str(body.get("workdir") or "").strip()
    if not workdir:
        return api_response(
            status="error", message="workdir is required (git repo within claude_cli.allowed_paths)", code=400
        )
    model = str(body.get("model") or "").strip() or None
    try:
        timeout = int(body.get("timeout") or 600)
    except (TypeError, ValueError):
        timeout = 600
    timeout = max(30, min(timeout, 3600))

    from agent.cli_backends.opencode import run_claude_write_armed

    started = time.time()
    result = run_claude_write_armed(prompt=prompt[:4000], model=model, timeout=timeout, workdir=workdir)
    duration_ms = int((time.time() - started) * 1000)
    audit_logger.info(
        f"Claude write_armed run: status={result.get('status')} changed_files={len(result.get('changed_files') or [])}",
        extra={
            "extra_fields": {
                "action": "claude_write_armed_run",
                "status": result.get("status"),
                "rc": result.get("rc"),
                "changed_files": len(result.get("changed_files") or []),
                "duration_ms": duration_ms,
            }
        },
    )
    result["duration_ms"] = duration_ms
    return api_response(data=result)


@sgpt_bp.route("/backends/claude_code/apply-diff", methods=["POST"])
@check_auth
def claude_apply_reviewed_diff():
    """Diff-Apply nach Review: wendet einen geprueften write_armed-Diff
    auf das Original-Workdir an (git apply --check, dann git apply).
    Es wird nicht committet — der Commit bleibt manuelle Entscheidung.
    """
    body = request.get_json(silent=True) or {}
    diff = str(body.get("diff") or "")
    if not diff.strip():
        return api_response(status="error", message="diff is required", code=400)
    workdir = str(body.get("workdir") or "").strip()
    if not workdir:
        return api_response(
            status="error", message="workdir is required (git repo within claude_cli.allowed_paths)", code=400
        )

    from agent.cli_backends.opencode import apply_reviewed_diff

    result = apply_reviewed_diff(diff=diff, workdir=workdir)
    audit_logger.info(
        f"Claude diff-apply: status={result.get('status')} changed_files={len(result.get('changed_files') or [])}",
        extra={
            "extra_fields": {
                "action": "claude_apply_reviewed_diff",
                "status": result.get("status"),
                "applied": bool(result.get("applied")),
                "changed_files": len(result.get("changed_files") or []),
            }
        },
    )
    code = {"applied": 200, "conflict": 409}.get(result.get("status"), 422)
    return api_response(data=result, code=code)


@sgpt_bp.route("/sessions", methods=["POST"])
@check_auth
@validate_request(SgptSessionCreateRequest)
def create_cli_session():
    policy = _cli_session_policy()
    if not policy["enabled"]:
        return api_response(status="error", message="cli_sessions_disabled", code=403)
    data = request.get_json(silent=True) or {}
    backend = _normalize_backend_name(data.get("backend") or settings.sgpt_execution_backend, default="ananta-worker")
    if backend == "auto":
        backend = "ananta-worker"
    if backend not in SUPPORTED_CLI_BACKENDS:
        return api_response(
            status="error", message=f"Invalid backend. Allowed: {sorted(SUPPORTED_CLI_BACKENDS)}", code=400
        )
    if backend not in set(policy["stateful_backends"]):
        return api_response(status="error", message="backend_not_stateful_enabled", code=400)
    session = get_cli_session_service().create_session(
        backend=backend,
        model=data.get("model"),
        metadata={
            **(data.get("metadata") if isinstance(data.get("metadata"), dict) else {}),
            "scope_kind": "conversation" if str(data.get("conversation_id") or "").strip() else "session",
            "scope_key": str(data.get("conversation_id") or "").strip() or f"session:{uuid.uuid4()}",
        },
        task_id=data.get("task_id"),
        conversation_id=data.get("conversation_id"),
    )
    if backend == "opencode" and policy.get("native_opencode_sessions"):
        from agent.services.opencode_runtime_service import get_opencode_runtime_service

        get_opencode_runtime_service().ensure_session_runtime(session, model=data.get("model"))
        session = get_cli_session_service().get_session(session["id"], include_history=False) or session
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
    backend = _normalize_backend_name(session.get("backend"), default="ananta-worker")
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
    if backend in {"sgpt", "ananta-worker"} and "--no-interaction" not in safe_options:
        safe_options.append("--no-interaction")
    policy = _cli_session_policy()
    effective_prompt = prompt
    if not _has_native_opencode_runtime(session):
        effective_prompt = (
            get_cli_session_service().build_prompt_with_history(
                session_id=session_id,
                prompt=prompt,
                max_turns=policy["max_turns_per_session"],
            )
            or prompt
        )
    task_kind = normalize_task_kind(data.get("task_kind"), prompt)
    rc, out, err, backend_used = run_llm_cli_command(
        effective_prompt,
        safe_options,
        backend=backend,
        model=data.get("model") or session.get("model"),
        routing_policy={"mode": "stateful_session", "task_kind": task_kind, "policy_version": "session-v1"},
        session=session,
    )
    if rc != 0 and not out:
        return api_response(
            status="error", message=err or f"backend '{backend_used}' failed with exit code {rc}", code=500
        )
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
    task_kind = normalize_task_kind(data.get("task_kind"), query)
    retrieval_intent = str(data.get("retrieval_intent") or "").strip() or None
    try:
        source_types = _parse_source_types(data.get("source_types"))
    except ValueError as e:
        return api_response(status="error", message=str(e), code=400)
    try:
        RAG_REQUESTS_TOTAL.labels(mode="context").inc()
        with RAG_RETRIEVAL_DURATION.time():
            payload = get_rag_service().retrieve_context_bundle(
                query,
                include_context_text=include_context_text,
                task_kind=task_kind,
                retrieval_intent=retrieval_intent,
                source_types=source_types,
            )
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
