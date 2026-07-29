"""Execution policy for the SGPT HTTP route.

The Flask route remains the composition boundary. This module owns the
request validation, retrieval, LoRA routing, backend invocation, and response
projection for one execution request.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from flask import current_app, g, request

from agent.cli_backends.sgpt import SUPPORTED_CLI_BACKENDS
from agent.common.errors import api_response
from agent.metrics import (
    RAG_CHUNKS_SELECTED,
    RAG_REQUESTS_TOTAL,
    RAG_RETRIEVAL_DURATION,
)
from agent.pipeline_trace import append_stage, new_pipeline_trace
from agent.research_artifact import normalize_research_artifact
from agent.research_backend import is_research_backend
from agent.services.ml_intern_lora_inference_service import (
    LoraInferenceRequest,
    LoraInferenceResult,
)

ALLOWED_BACKENDS = {*SUPPORTED_CLI_BACKENDS, "auto"}
BACKEND_ALIASES = {
    "ananta_worker": "ananta-worker",
    "shellgpt": "sgpt",
}


@dataclass(frozen=True)
class SgptExecutePolicy:
    """Patchable policy dependencies supplied by the route composition root."""

    supported_backends: set[str]
    allowed_backends: Callable[[], set[str]]
    normalize_backend_name: Callable[..., str]
    extract_user_id: Callable[[], str]
    parse_source_types: Callable[[Any], list[str] | None]
    normalize_task_kind: Callable[..., str]
    runtime_routing_config: Callable[..., dict[str, Any]]
    resolve_cli_backend: Callable[..., tuple[str, str, dict[str, Any]]]
    normalize_backend_flags: Callable[..., tuple[list[str], list[str]]]
    resolve_lora_adapter_routing: Callable[..., dict[str, Any]]
    build_trace_record: Callable[..., dict[str, Any]]


@dataclass(frozen=True)
class SgptExecuteRuntime:
    settings: Any
    policy: SgptExecutePolicy
    circuit_breaker: dict[str, Any]
    cb_threshold: int
    cb_recovery_time: int
    is_rate_limited: Callable[[str], bool]
    get_context_manager_service: Callable[[], Any]
    get_lora_inference_service: Callable[[], Any]
    get_ml_intern_adapter_service: Callable[[], Any]
    run_llm_cli_command: Callable[..., tuple[int, str, str, str]]
    get_logger: Callable[[], Any]
    audit_logger: Any


def normalize_backend_name(
    value: str | None,
    *,
    default: str = "ananta-worker",
    aliases: Mapping[str, str] | None = None,
) -> str:
    backend = str(value or "").strip().lower()
    if not backend:
        backend = default
    active_aliases = BACKEND_ALIASES if aliases is None else aliases
    return active_aliases.get(backend, backend)


def extract_user_id() -> str:
    user_id = request.remote_addr or "unknown"
    if hasattr(g, "user") and isinstance(g.user, dict):
        user_id = g.user.get(
            "sub",
            g.user.get("user_id", user_id),
        )
    elif hasattr(g, "auth_payload") and isinstance(g.auth_payload, dict):
        user_id = g.auth_payload.get("sub", user_id)
    return str(user_id)


def parse_source_types(value: Any) -> list[str] | None:
    allowed = {"repo", "artifact", "task_memory", "wiki"}
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("source_types must be a list of strings")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        source_type = str(item or "").strip().lower()
        if not source_type:
            continue
        if source_type not in allowed:
            raise ValueError(f"invalid source_types value: {source_type}")
        if source_type not in seen:
            seen.add(source_type)
            normalized.append(source_type)
    return normalized or None


def _resolve_requested_base_model(
    model: str | None,
    agent_cfg: dict[str, Any],
    *,
    settings: Any,
) -> str:
    return str(
        model
        or agent_cfg.get("sgpt_default_model")
        or agent_cfg.get("default_model")
        or agent_cfg.get("model")
        or settings.sgpt_default_model
        or ""
    ).strip()


def _public_lora_provenance(
    provenance: dict[str, Any],
) -> dict[str, Any]:
    return {key: value for key, value in dict(provenance or {}).items() if not str(key).startswith("_")}


def _lora_registry_scope() -> dict[str, str]:
    identity = dict(getattr(g, "user", {}) or getattr(g, "auth_payload", {}) or {})
    subject = str(identity.get("sub") or identity.get("username") or identity.get("agent_id") or "hub-admin").strip()
    tenant = str(identity.get("tenant_id") or identity.get("tenant") or subject).strip()
    return {
        "tenant_id": tenant,
        "owner_subject": subject,
    }


def allowed_backends(configured_backends: set[str]) -> set[str]:
    allowed = set(configured_backends)
    cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    spike_cfg = cfg.get("ml_intern_spike") if isinstance(cfg.get("ml_intern_spike"), dict) else {}
    if bool(spike_cfg.get("enabled", False)):
        allowed.add("ml_intern")
    return allowed


def _build_cli_error_details(
    errors: str,
    backend_used: str,
) -> dict[str, Any] | None:
    message = str(errors or "")
    lowered = message.lower()
    if "cannot truncate prompt with n_keep" in lowered and "n_ctx" in lowered:
        return {
            "type": "context_limit_mismatch",
            "backend": backend_used,
            "hint": (
                "Model context window is too small for prompt/tool "
                "preamble. Increase context_limit or choose a model "
                "with larger n_ctx."
            ),
        }
    return None


def execute_sgpt_request(  # noqa: C901
    runtime: SgptExecuteRuntime,
):
    """Execute one validated SGPT HTTP request."""

    circuit_breaker = runtime.circuit_breaker
    if circuit_breaker["open"]:
        if time.time() - circuit_breaker["last_failure"] > runtime.cb_recovery_time:
            runtime.get_logger().info("SGPT circuit breaker switching to half-open.")
            circuit_breaker["open"] = False
            circuit_breaker["failures"] = 0
        else:
            return api_response(
                status="error",
                message=("SGPT service is temporarily unavailable (circuit breaker open)."),
                code=503,
            )

    user_id = runtime.policy.extract_user_id()
    if runtime.is_rate_limited(user_id):
        runtime.get_logger().warning(
            "Rate limit exceeded for user %s",
            user_id,
        )
        return api_response(
            status="error",
            message="Rate limit exceeded. Please try again later.",
            code=429,
        )

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return api_response(
            status="error",
            message="Invalid JSON payload",
            code=400,
        )

    prompt = data.get("prompt")
    options = data.get("options", [])
    use_hybrid_context = bool(data.get("use_hybrid_context", False))
    backend = runtime.policy.normalize_backend_name(
        data.get("backend") or runtime.settings.sgpt_execution_backend,
        default="ananta-worker",
    )
    model = data.get("model")
    task_kind = runtime.policy.normalize_task_kind(
        data.get("task_kind"),
        prompt or "",
    )
    retrieval_intent = str(data.get("retrieval_intent") or "").strip() or None
    try:
        source_types = runtime.policy.parse_source_types(data.get("source_types"))
    except ValueError as exc:
        return api_response(
            status="error",
            message=str(exc),
            code=400,
        )

    if not prompt:
        return api_response(
            status="error",
            message="Missing prompt",
            code=400,
        )
    if not isinstance(options, list):
        return api_response(
            status="error",
            message="Options must be a list",
            code=400,
        )
    allowed_backends = runtime.policy.allowed_backends()
    if backend not in allowed_backends:
        return api_response(
            status="error",
            message=(f"Invalid backend. Allowed: {sorted(allowed_backends)}"),
            code=400,
        )
    if model is not None and not isinstance(model, str):
        return api_response(
            status="error",
            message="model must be a string",
            code=400,
        )
    if not all(isinstance(option, str) for option in options):
        return api_response(
            status="error",
            message="options must contain only strings",
            code=400,
        )

    agent_cfg = current_app.config.get("AGENT_CONFIG", {}) or {}
    routing_reason = ""
    if backend == "ml_intern":
        effective_backend = "ml_intern"
        routing_reason = "specialized_profile_ml_intern"
        routing_cfg = runtime.policy.runtime_routing_config(agent_cfg)
    else:
        (
            effective_backend,
            routing_reason,
            routing_cfg,
        ) = runtime.policy.resolve_cli_backend(
            task_kind=task_kind,
            requested_backend=backend,
            supported_backends=runtime.policy.supported_backends,
            agent_cfg=agent_cfg,
            fallback_backend="ananta-worker",
        )
    if effective_backend == "ml_intern":
        safe_options = []
        if options:
            return api_response(
                status="error",
                message=("ml_intern backend does not accept CLI flags"),
                code=400,
            )
    else:
        safe_options, rejected = runtime.policy.normalize_backend_flags(
            effective_backend,
            options,
        )
        if rejected:
            return api_response(
                status="error",
                message=(f"Unsupported options for backend '{effective_backend}': {rejected}"),
                code=400,
            )
        if effective_backend in {"sgpt", "ananta-worker"} and "--no-interaction" not in safe_options:
            safe_options.append("--no-interaction")

    try:
        context_payload = None
        effective_prompt = prompt
        degraded = False
        grounding = {
            "score": 0.0,
            "chunk_count": 0,
            "engine_diversity": 0,
        }
        pipeline = new_pipeline_trace(
            pipeline="sgpt_execute",
            task_kind=task_kind,
            policy_version=routing_cfg["policy_version"],
            metadata={"requested_backend": backend},
        )
        if use_hybrid_context:
            stage_started = time.time()
            if not runtime.settings.rag_enabled:
                return api_response(
                    status="error",
                    message="Hybrid context mode is disabled",
                    code=400,
                )
            RAG_REQUESTS_TOTAL.labels(mode="execute").inc()
            with RAG_RETRIEVAL_DURATION.time():
                (
                    context_payload,
                    effective_prompt,
                ) = runtime.get_context_manager_service().build_cli_execution_context(
                    prompt=prompt,
                    task_kind=task_kind,
                    retrieval_intent=retrieval_intent,
                    source_types=source_types,
                )
            chunk_count = len(context_payload.get("chunks", []))
            RAG_CHUNKS_SELECTED.observe(chunk_count)
            engines = {str((chunk or {}).get("engine") or "") for chunk in (context_payload.get("chunks") or [])}
            diversity = len([engine for engine in engines if engine])
            score = min(
                1.0,
                (chunk_count / max(1, runtime.settings.rag_max_chunks)) * 0.7 + min(diversity, 3) / 3.0 * 0.3,
            )
            grounding = {
                "score": round(score, 3),
                "chunk_count": chunk_count,
                "engine_diversity": diversity,
            }
            if chunk_count == 0:
                degraded = True
            append_stage(
                pipeline,
                name="retrieve",
                status=("ok" if chunk_count > 0 else "degraded"),
                metadata={
                    "chunk_count": chunk_count,
                    "engine_diversity": diversity,
                },
                started_at=stage_started,
            )
        else:
            append_stage(
                pipeline,
                name="retrieve",
                status="skipped",
                metadata={"use_hybrid_context": False},
            )

        append_stage(
            pipeline,
            name="route",
            status="ok",
            metadata={
                "requested_backend": backend,
                "effective_backend": effective_backend,
                "reason": routing_reason,
            },
        )

        stage_started = time.time()
        lora_scope = _lora_registry_scope()
        lora_provenance = runtime.policy.resolve_lora_adapter_routing(
            task_kind=task_kind,
            base_model=_resolve_requested_base_model(
                model,
                agent_cfg,
                settings=runtime.settings,
            ),
            agent_cfg=agent_cfg,
            **lora_scope,
        )
        lora_handled = False
        if effective_backend != "ml_intern" and lora_provenance.get("adapter_used"):
            append_stage(
                pipeline,
                name="lora_route",
                status="ok",
                metadata=_public_lora_provenance(lora_provenance),
            )
            try:
                inference_result = runtime.get_lora_inference_service().generate(
                    LoraInferenceRequest(
                        prompt=effective_prompt,
                        base_model=str(lora_provenance.get("base_model") or ""),
                        adapter_id=str(lora_provenance.get("adapter_id") or ""),
                        adapter_version=str(lora_provenance.get("adapter_version") or ""),
                        task_kind=task_kind,
                        task_id=str(data.get("task_id") or (f"sgpt-{uuid.uuid4().hex}")),
                    ),
                    **lora_scope,
                )
                output = (
                    inference_result.text
                    if isinstance(
                        inference_result,
                        LoraInferenceResult,
                    )
                    else str(inference_result)
                )
                returncode = 0
                errors = ""
                backend_used = "lora_adapter"
                lora_handled = True
                if isinstance(
                    inference_result,
                    LoraInferenceResult,
                ):
                    lora_provenance["runtime"] = {
                        "worker_id": (inference_result.worker_id),
                        "capability": (inference_result.capability),
                        "reason_code": (inference_result.reason_code),
                    }
                append_stage(
                    pipeline,
                    name="lora_infer",
                    status="ok",
                    metadata={
                        "adapter_id": (lora_provenance.get("adapter_id")),
                        "adapter_version": (lora_provenance.get("adapter_version")),
                        "reason_code": ("approved_adapter_worker_dispatch"),
                    },
                    started_at=stage_started,
                )
            except Exception as exc:
                reason_code = str(
                    getattr(
                        exc,
                        "reason_code",
                        "adapter_inference_failed",
                    )
                )
                append_stage(
                    pipeline,
                    name="lora_infer",
                    status="degraded",
                    metadata={
                        "reason_code": reason_code,
                        "error_type": type(exc).__name__,
                    },
                    started_at=stage_started,
                )
                degraded = True
                if not bool(
                    lora_provenance.get(
                        "fallback_to_base_model",
                        True,
                    )
                ):
                    blocked_reason = "lora_adapter_failed_no_base_fallback"
                    lora_provenance["reason"] = blocked_reason
                    lora_provenance["reason_code"] = blocked_reason
                    lora_provenance["adapter_inference_error_code"] = reason_code
                    lora_provenance["policy_decision"] = {
                        **dict(lora_provenance.get("policy_decision") or {}),
                        "decision": "blocked",
                        "reason_code": blocked_reason,
                    }
                    return api_response(
                        status="error",
                        message=("approved LoRA adapter inference failed and base fallback is disabled"),
                        data={"lora_provenance": (_public_lora_provenance(lora_provenance))},
                        code=(
                            503
                            if bool(
                                getattr(
                                    exc,
                                    "retryable",
                                    False,
                                )
                            )
                            else 500
                        ),
                    )
                fallback_reason = "lora_adapter_failed_fell_back_to_base_model"
                lora_provenance["reason"] = fallback_reason
                lora_provenance["reason_code"] = fallback_reason
                lora_provenance["adapter_inference_error_code"] = reason_code
                lora_provenance["policy_decision"] = {
                    **dict(lora_provenance.get("policy_decision") or {}),
                    "decision": "base_model_fallback",
                    "reason_code": fallback_reason,
                }
        else:
            append_stage(
                pipeline,
                name="lora_route",
                status="skipped",
                metadata=_public_lora_provenance(lora_provenance),
            )
            if (
                effective_backend != "ml_intern"
                and not bool(lora_provenance.get("adapter_used"))
                and not bool(
                    lora_provenance.get(
                        "fallback_to_base_model",
                        True,
                    )
                )
            ):
                blocked_reason = "no_approved_adapter_and_base_fallback_disabled"
                lora_provenance["reason"] = blocked_reason
                lora_provenance["reason_code"] = blocked_reason
                lora_provenance["policy_decision"] = {
                    **dict(lora_provenance.get("policy_decision") or {}),
                    "decision": "blocked",
                    "reason_code": blocked_reason,
                }
                return api_response(
                    status="error",
                    message=("no approved compatible LoRA adapter is available and base fallback is disabled"),
                    data={"lora_provenance": (_public_lora_provenance(lora_provenance))},
                    code=409,
                )

        if effective_backend == "ml_intern":
            invocation = runtime.get_ml_intern_adapter_service().invoke_spike(
                prompt=effective_prompt,
                agent_cfg=agent_cfg,
                model=model,
            )
            returncode = 0 if bool(invocation.get("ok")) else 1
            output = str(invocation.get("stdout") or "")
            errors = str(invocation.get("stderr") or invocation.get("error") or "")
            backend_used = "ml_intern"
        elif not lora_handled:
            (
                returncode,
                output,
                errors,
                backend_used,
            ) = runtime.run_llm_cli_command(
                effective_prompt,
                safe_options,
                backend=effective_backend,
                model=model,
                routing_policy={
                    "mode": "adaptive",
                    "task_kind": task_kind,
                    "policy_version": (routing_cfg["policy_version"]),
                },
            )
        append_stage(
            pipeline,
            name="execute",
            status=("ok" if returncode == 0 or bool(output) else "error"),
            metadata={
                "backend_used": backend_used,
                "returncode": returncode,
            },
            started_at=stage_started,
        )
        if returncode != 0 and not output:
            runtime.get_logger().error(
                "LLM CLI (%s) Return Code %s: %s",
                backend_used,
                returncode,
                errors,
            )
            circuit_breaker["failures"] += 1
            circuit_breaker["last_failure"] = time.time()
            if circuit_breaker["failures"] >= runtime.cb_threshold:
                circuit_breaker["open"] = True
                runtime.get_logger().error("SGPT CIRCUIT BREAKER OPEN")
            details = _build_cli_error_details(
                errors,
                backend_used,
            )
            return api_response(
                status="error",
                message=(errors or (f"LLM CLI ({backend_used}) failed with exit code {returncode}")),
                data=({"diagnostics": details} if details else None),
                code=500,
            )

        circuit_breaker["failures"] = 0
        circuit_breaker["open"] = False
        safe_output = output or ""
        safe_errors = errors or ""
        runtime.audit_logger.info(
            f"SGPT Success: output_len={len(safe_output)}",
            extra={
                "extra_fields": {
                    "action": "sgpt_success",
                    "output_len": len(safe_output),
                    "error_len": len(safe_errors),
                }
            },
        )
        trace = runtime.policy.build_trace_record(
            task_id=None,
            event_type="sgpt_execute",
            task_kind=task_kind,
            backend=backend_used,
            requested_backend=backend,
            routing_reason=routing_reason,
            policy_version=routing_cfg["policy_version"],
            metadata={
                "degraded": degraded,
                "context_used": (context_payload is not None),
            },
        )
        response_data = {
            "trace_id": trace["trace_id"],
            "trace": trace,
            "pipeline": {
                **pipeline,
                "trace_id": trace["trace_id"],
            },
            "output": safe_output,
            "errors": safe_errors,
            "backend": backend_used,
            "routing": {
                "policy_version": (routing_cfg["policy_version"]),
                "task_kind": task_kind,
                "requested_backend": backend,
                "effective_backend": effective_backend,
                "reason": routing_reason,
                "confidence": (0.9 if backend != "auto" else 0.75),
            },
            "fallback": {
                "degraded_mode": degraded,
                "reason": ("no_context_chunks" if degraded else None),
            },
            "grounding": grounding,
        }
        if is_research_backend(backend_used):
            response_data["research_artifact"] = normalize_research_artifact(
                safe_output,
                backend=backend_used,
                cli_result={
                    "stderr_preview": safe_errors[:240],
                    "returncode": returncode,
                },
            )
        if lora_provenance.get("adapter_used"):
            response_data["lora_provenance"] = _public_lora_provenance(lora_provenance)
        else:
            response_data["adapter_used"] = False
        if context_payload is not None:
            response_data["context"] = {
                "strategy": context_payload.get(
                    "strategy",
                    {},
                ),
                "policy_version": context_payload.get(
                    "policy_version",
                    "v1",
                ),
                "chunk_count": len(context_payload.get("chunks", [])),
                "token_estimate": context_payload.get(
                    "token_estimate",
                    0,
                ),
            }
        return api_response(data=response_data)
    except Exception as exc:
        runtime.get_logger().exception("Error executing SGPT")
        runtime.audit_logger.error(
            f"SGPT Error: {str(exc)}",
            extra={
                "extra_fields": {
                    "action": "sgpt_error",
                    "error": str(exc),
                }
            },
        )
        return api_response(
            status="error",
            message=str(exc),
            code=500,
        )
