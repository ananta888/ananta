"""Forwarding-hub cluster for the task-scoped execution service.

Extracted from ``agent.services.task_scoped_execution_service`` as the
forwarding_hub cluster of SPLIT-001 (sub-split 001f). The module owns
cross-container task forwarding: deciding whether to forward to a remote
worker, persisting forwarded proposal/execution results, and normalizing
forwarded artifacts.

Backwards compatibility is preserved at the service boundary via thin
delegating wrappers in :class:`TaskScopedExecutionService` (12-month
deprecation window, see todos/todo.refactor-large-files-split.json SPLIT-001).
"""

from __future__ import annotations

import copy
import hashlib
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

from flask import current_app

from agent.common.api_envelope import unwrap_api_envelope
from agent.common.errors import WorkerForwardingError
from agent.config import settings
from agent.llm_integration import normalize_llm_call_profile_entry
from agent.services._vector_index_result_forwarding import (
    accept_bound_forwarded_vector_index_result,
    accept_forwarded_vector_index_result,
    is_authoritative_vector_index_task,
    vector_index_result_candidate,
)
from agent.services._vector_index_result_forwarding import (
    persist_forwarded_execution_status as _persist_forwarded_execution_status,
)
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services
from agent.services.task_runtime_service import update_local_task_status

if TYPE_CHECKING:
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse


def _governed_source_control_index_job_service() -> Any:
    service = current_app.extensions.get(
        "source_control_governed_knowledge_index_job_service"
    )
    if service is None:
        raise WorkerForwardingError(
            "knowledge_index_dispatch_authorizer_unavailable"
        )
    return service


_VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS: dict[str, tuple[str, frozenset[str]]] = {
    "visual_process_assistant_retrieval": (
        "ananta.visual_process_assistant.retrieval_result.v1",
        frozenset(
            {
                "schema",
                "task_id",
                "request_id",
                "context_id",
                "status",
                "evidence",
                "rejected_count",
                "rejection_reasons",
                "consistency_state",
                "blocked_stubs",
                "evidence_conflicts",
            }
        ),
    ),
    "visual_process_assistant_inference": (
        "ananta.visual_process_assistant.inference_result.v1",
        frozenset(
            {
                "schema",
                "task_id",
                "request_id",
                "context_id",
                "prompt_hash",
                "status",
                "reason_code",
                "response",
                "model_metadata",
            }
        ),
    ),
}
_VISUAL_PROCESS_ASSISTANT_RESULT_SCHEMAS = frozenset(
    schema for schema, _fields in _VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS.values()
)
_FORWARDED_HANDLER_FRAMEWORK_FIELDS = frozenset({"handler_contract"})


def _get_visual_process_assistant_service() -> Any:
    """Resolve the Hub-owned service lazily and avoid a forwarding import cycle."""

    from agent.services.visual_process_assistant_service import (
        visual_process_assistant_service,
    )

    return visual_process_assistant_service


def _accept_visual_process_assistant_result(
    *,
    tid: str,
    response: Mapping[str, Any],
    task: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Dispatch a bound Visual Process worker result to its Hub owner.

    A Visual Process schema or task kind activates this fail-closed boundary.
    Framework metadata is validated separately and is never passed into the
    domain result contract.
    """

    task_kind = str(task.get("task_kind") or "").strip().lower()
    schema = str(response.get("schema") or "").strip()
    expected = _VISUAL_PROCESS_ASSISTANT_RESULT_CONTRACTS.get(task_kind)
    if expected is None and schema not in _VISUAL_PROCESS_ASSISTANT_RESULT_SCHEMAS:
        return None
    if expected is None:
        raise ValueError("visual_process_assistant_result_task_kind_unknown")
    expected_schema, result_fields = expected
    if schema != expected_schema:
        raise ValueError("visual_process_assistant_result_schema_kind_mismatch")

    handler_contract = response.get("handler_contract")
    if handler_contract is not None:
        if not isinstance(handler_contract, Mapping):
            raise ValueError("visual_process_assistant_handler_contract_invalid")
        handler_task_kind = str(handler_contract.get("task_kind") or "").strip().lower()
        if handler_task_kind != task_kind:
            raise ValueError("visual_process_assistant_handler_task_kind_mismatch")

    unknown_fields = set(response) - result_fields - _FORWARDED_HANDLER_FRAMEWORK_FIELDS
    if unknown_fields:
        raise ValueError("visual_process_assistant_result_forwarding_fields_unknown")
    candidate = {field: response.get(field) for field in result_fields}
    accepted = _get_visual_process_assistant_service().accept_worker_result(
        task_id=tid,
        result=candidate,
    )
    if not isinstance(accepted, Mapping):
        raise ValueError("visual_process_assistant_acceptance_readmodel_invalid")
    return dict(accepted)


def forward_task_request_if_remote(
    *,
    tid: str,
    task: dict,
    endpoint: str,
    payload: dict,
    forwarder: Callable,
    on_success: Callable[[dict, dict], None],
) -> "TaskScopedRouteResponse | None":
    from agent.services.task_scoped_execution_service import TaskScopedRouteResponse

    mail_lease: dict[str, Any] | None = None
    mail_lease_owner: str | None = None
    preserve_mail_lease_on_error = False

    def release_mail_lease() -> None:
        nonlocal mail_lease
        if mail_lease is None:
            return
        try:
            from agent.services.mail_task_service import get_mail_task_service

            get_mail_task_service().release_lease(
                job_id=tid,
                fencing_token=int(mail_lease["fencing_token"]),
                owner_ref=mail_lease_owner,
            )
        finally:
            mail_lease = None

    # Hub owns cross-container routing. Worker containers must execute locally
    # and never re-forward step endpoints to avoid forwarding loops.
    if str(getattr(settings, "role", "") or "").strip().lower() != "hub":
        return None
    worker_url = task.get("assigned_agent_url")
    if not worker_url:
        return None
    my_url = settings.agent_url or f"http://localhost:{settings.port}"
    if worker_url.rstrip("/") == my_url.rstrip("/"):
        return None
    try:
        parsed_worker = urlparse(str(worker_url))
        parsed_self = urlparse(str(my_url))
        worker_host = str(parsed_worker.hostname or "").strip().lower()
        self_host = str(parsed_self.hostname or "").strip().lower()
        worker_port = int(parsed_worker.port or settings.port)
        self_port = int(parsed_self.port or settings.port)
        if worker_port == self_port and (
            worker_host in {"localhost", "127.0.0.1", "0.0.0.0"} or worker_host == self_host
        ):
            return None
    except Exception:
        pass
    payload = dict(payload)
    is_vector_index_task = is_authoritative_vector_index_task(
        task
    )
    if is_vector_index_task:
        dispatch_phase = (
            "propose"
            if endpoint.rstrip("/").endswith("/step/propose")
            else "execute"
        )
        from agent.services.vector_index_task_service import (
            get_vector_index_task_service,
        )

        payload["vector_index_dispatch"] = (
            get_vector_index_task_service().issue_dispatch_attempt(
                job_id=tid,
                worker_audience=str(worker_url),
                phase=dispatch_phase,
                actor="hub-worker-forwarder",
            )
        )
    assigned_token = task.get("assigned_agent_token")
    resolved_token = assigned_token
    dispatch_lease_token = str(
        payload.get("dispatch_lease_token") or ""
    ).strip()
    dispatch_phase = str(
        payload.get("dispatch_lease_phase")
        or (
            "propose"
            if endpoint.endswith("/step/propose")
            else "execute"
        )
    ).strip().lower()
    from agent.services.recovery_dispatch_gate_service import (
        get_recovery_dispatch_gate_service,
        recovery_dispatch_request_fingerprint,
    )

    recovery_gate = get_recovery_dispatch_gate_service()
    recovery_child = recovery_gate.is_recovery_child(task)
    recovery_fenced = bool(
        dispatch_lease_token
        or recovery_child
    )
    if recovery_child and not dispatch_lease_token:
        return TaskScopedRouteResponse(
            data={
                "status": "skipped",
                "reason": "recovery_dispatch_lease_missing",
                "task_id": tid,
                "phase": dispatch_phase,
            },
            status="skipped",
            message="Recovery dispatch requires a lease",
            code=409,
        )
    if (
        str(task.get("task_kind") or "").strip().lower() == "mail_operation"
        and str(endpoint or "").rstrip("/").endswith("/execute")
    ):
        from agent.services.mail_task_service import get_mail_task_service

        mail_lease_owner = (
            "hub-worker:"
            + hashlib.sha256(str(worker_url).encode("utf-8")).hexdigest()[:24]
        )
        mail_lease = get_mail_task_service().claim_for_delegation(
            job_id=tid,
            owner_ref=mail_lease_owner,
        )
        if mail_lease is None:
            raise WorkerForwardingError(
                details={
                    "details": "mail_task_account_lease_unavailable",
                    "worker_url": worker_url,
                }
            )
    registered_agent = None
    try:
        registered_agent = get_repository_registry().agent_repo.get_by_url(
            worker_url
        )
        current_token = str(
            getattr(registered_agent, "token", "") or ""
        ).strip()
        if current_token:
            resolved_token = current_token
    except Exception:
        pass
    worker_execution_context = task.get("worker_execution_context")
    if (
        str(task.get("task_kind") or "").strip().lower()
        == "codecompass_index_build"
        and isinstance(worker_execution_context, dict)
    ):
        knowledge_index_job = worker_execution_context.get(
            "knowledge_index_job"
        )
        if (
            isinstance(knowledge_index_job, dict)
            and knowledge_index_job.get("schema")
            == "ananta.knowledge_index_execution_job.v2"
            and not knowledge_index_job.get(
                "source_access_enforcement_manifest"
            )
        ):
            if registered_agent is None:
                raise WorkerForwardingError(
                    "assigned_worker_not_registered"
                )
            if not getattr(
                registered_agent,
                "registration_validated",
                False,
            ):
                raise WorkerForwardingError(
                    "assigned_worker_registration_not_validated"
                )
            if (
                str(getattr(registered_agent, "role", ""))
                .strip()
                .lower()
                != "worker"
            ):
                raise WorkerForwardingError(
                    "assigned_agent_is_not_worker"
                )

            destination_selection = worker_execution_context.get(
                "destination_selection"
            )
            if (
                not isinstance(destination_selection, dict)
                or not destination_selection
            ):
                raise WorkerForwardingError(
                    "knowledge_index_destination_selection_missing"
                )
            knowledge_index_job_service = (
                _governed_source_control_index_job_service()
            )
            authorized_context = (
                knowledge_index_job_service.authorize_bound_worker_dispatch(
                    job_id=tid,
                    authenticated_worker_id=str(
                        registered_agent.name or registered_agent.url
                    ),
                    destination_selection=destination_selection,
                )
            )
            task["worker_execution_context"] = {
                **worker_execution_context,
                **authorized_context,
            }
    try:
        response = forwarder(worker_url, endpoint, payload, token=resolved_token)
        if (
            response is None
            and resolved_token
            and not recovery_fenced
            and not is_vector_index_task
        ):
            response = forwarder(worker_url, endpoint, payload, token=None)
        if (
            resolved_token
            and not recovery_fenced
            and not is_vector_index_task
            and isinstance(response, dict)
            and str(response.get("status") or "").strip().lower() == "error"
            and (
                "401" in str(response.get("message") or "").lower()
                or "unauthorized" in str(response.get("message") or "").lower()
            )
        ):
            response = forwarder(worker_url, endpoint, payload, token=None)
        # Worker returned 404: task not in worker DB (split-DB dev setup).
        # Configurable via execution_fallback_policy.worker_404_hub_fallback_enabled.
        if (
            isinstance(response, dict)
            and str(response.get("status") or "").strip().lower() == "error"
            and int(response.get("http_status") or 0) == 404
            and not recovery_fenced
            and not is_vector_index_task
        ):
            _fallback_policy = {}
            try:
                _fallback_policy = dict(
                    current_app.config.get("AGENT_CONFIG", {}).get("execution_fallback_policy") or {}
                )
            except Exception:
                pass
            if bool(_fallback_policy.get("worker_404_hub_fallback_enabled", True)):
                release_mail_lease()
                current_app.logger.warning(
                    "Worker %s returned 404 for %s — falling back to local hub execution",
                    worker_url,
                    endpoint,
                )
                return None
        response = unwrap_api_envelope(response)
        if not isinstance(response, dict) or not response:
            raise RuntimeError(f"worker_empty_payload:{worker_url}:{endpoint}")
        if isinstance(response, dict):
            if recovery_fenced:
                rejected_response = None
                with recovery_gate.result_guard(
                    tid,
                    token=dispatch_lease_token or None,
                    phase=dispatch_phase,
                    request_fingerprint=(
                        recovery_dispatch_request_fingerprint(
                            dispatch_phase,
                            payload,
                        )
                    ),
                    worker_url=str(worker_url),
                ) as decision:
                    if not decision.allowed:
                        rejected_response = TaskScopedRouteResponse(
                            data={
                                "status": "skipped",
                                "reason": decision.reason_code,
                                "task_id": tid,
                                "phase": dispatch_phase,
                            },
                            status="skipped",
                            message=(
                                "Recovery dispatch result rejected"
                            ),
                            code=409,
                        )
                    else:
                        preserve_mail_lease_on_error = bool(
                            mail_lease is not None
                        )
                        on_success(response, task)
                if rejected_response is not None:
                    release_mail_lease()
                    return rejected_response
                preserve_mail_lease_on_error = False
            else:
                on_success(response, task)
            release_mail_lease()
        return TaskScopedRouteResponse(data=response)
    except Exception as exc:
        err_text = str(exc or "")
        err_lc = err_text.lower()
        if (
            assigned_token
            and not recovery_fenced
            and not is_vector_index_task
            and ("401" in err_lc or "unauthorized" in err_lc)
        ):
            try:
                response = forwarder(worker_url, endpoint, payload, token=None)
                response = unwrap_api_envelope(response)
                if isinstance(response, dict):
                    on_success(response, task)
                    release_mail_lease()
                return TaskScopedRouteResponse(data=response)
            except Exception:
                pass
        if preserve_mail_lease_on_error:
            current_app.logger.warning(
                "Recovery mail lease retained after result commit failure for task %s",
                tid,
            )
        else:
            release_mail_lease()
        current_app.logger.error("Forwarding an %s fehlgeschlagen: %s", worker_url, exc)
        raise WorkerForwardingError(details={"details": str(exc), "worker_url": worker_url})


def persist_forwarded_proposal(
    response: dict,
    task: dict,
    request_payload: dict | None = None,
    *,
    allow_synthetic_llm_profile_fallback: Callable[[], bool],
) -> None:
    if not isinstance(response, dict):
        return
    from agent.services.recovery_task_mutation_policy import (
        recovery_task_role,
    )
    from agent.services.recovery_worker_result_service import (
        get_recovery_worker_result_service,
    )

    recovery_role = recovery_task_role(task)
    worker_result_service = get_recovery_worker_result_service()
    if recovery_role == "child":
        # Validate before proposal/history persistence.  A malformed Worker
        # envelope must have no authoritative Hub side effect.
        worker_result_service.merge_response(
            task_id=str(task["id"]),
            phase="propose",
            response=response,
            verification_status=task.get("verification_status"),
        )
    elif response.get("recovery_worker_result") is not None:
        raise ValueError("recovery_worker_result_unexpected")
    request_payload = dict(request_payload or {})
    prompt_text = str(request_payload.get("prompt") or "").strip()
    forwarded_request = {
        "prompt_preview": prompt_text[:240],
        "prompt_hash_sha256": hashlib.sha256(prompt_text.encode("utf-8")).hexdigest() if prompt_text else None,
        "provider": str(request_payload.get("provider") or "").strip() or None,
        "providers": (
            list(request_payload.get("providers") or [])
            if isinstance(request_payload.get("providers"), list)
            else None
        ),
        "model": str(request_payload.get("model") or "").strip() or None,
        "temperature": request_payload.get("temperature"),
        "strategy_mode": str(request_payload.get("strategy_mode") or "").strip() or None,
        "request_task_id": str(request_payload.get("task_id") or "").strip() or None,
        "captured_at": time.time(),
    }
    has_proposal_payload = any(
        key in response
        for key in (
            "command",
            "tool_calls",
            "reason",
            "raw",
            "routing",
            "cli_result",
            "trace",
            "review",
            "pipeline",
            "research_artifact",
            "research_context",
            "worker_context",
        )
    )
    if not has_proposal_payload:
        return
    response_trace = response.get("trace") if isinstance(response.get("trace"), dict) else None
    if not response_trace:
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        wrapped = response.get("proposal") if isinstance(response.get("proposal"), dict) else {}
        wrapped_meta = wrapped.get("metadata") if isinstance(wrapped.get("metadata"), dict) else {}
        prompt_trace_id = (
            str(metadata.get("prompt_trace_id") or "").strip()
            or str(wrapped_meta.get("prompt_trace_id") or "").strip()
        )
        if prompt_trace_id:
            response_trace = {
                "trace_id": prompt_trace_id,
                "source": "model_invocation_service",
                "request_kind": "propose",
            }
        else:
            response_trace = {
                "source": "external_worker_uninspectable",
                "request_kind": "propose",
                "external_worker_uninspectable": True,
            }
    cli_result = response.get("cli_result") if isinstance(response.get("cli_result"), dict) else None
    if not isinstance(cli_result, dict):
        response_meta = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        meta_profile = [
            normalize_llm_call_profile_entry(entry)
            for entry in list(response_meta.get("llm_call_profile") or [])
            if isinstance(entry, dict)
        ]
        if meta_profile:
            cli_result = {
                "returncode": 0,
                "latency_ms": None,
                "output_source": str(response.get("backend") or "orchestrator").strip() or "orchestrator",
                "llm_call_profile": meta_profile,
            }
    if cli_result is None:
        snapshot = get_core_services().autopilot_decision_service.build_proposal_snapshot(response)
        snapshot_cli = snapshot.get("cli_result") if isinstance(snapshot.get("cli_result"), dict) else None
        if isinstance(snapshot_cli, dict):
            cli_result = dict(snapshot_cli)
    if not isinstance(cli_result, dict) and allow_synthetic_llm_profile_fallback():
        backend = str(response.get("backend") or "orchestrator").strip() or "orchestrator"
        model = str(response.get("model") or "").strip() or None
        provider = None
        ms = response.get("model_selection")
        if isinstance(ms, dict):
            provider = str(ms.get("runtime_provider") or "").strip() or None
            model = model or (str(ms.get("selected_model") or "").strip() or None)
        cli_result = {
            "returncode": 0,
            "latency_ms": None,
            "output_source": backend,
            "llm_call_profile": [
                {
                    "name": "propose_forwarded",
                    "backend": backend,
                    "provider": provider,
                    "model": model,
                    "success": True,
                    "latency_ms": None,
                    "prompt_tokens": None,
                    "completion_tokens": None,
                    "total_tokens": None,
                    "source": "orchestrator_synthetic",
                    "estimated": True,
                    "error_type": None,
                    "error_message": None,
                    "started_at": None,
                    "ended_at": None,
                }
            ],
        }
    if not isinstance(cli_result, dict):
        cli_result = {
            "returncode": 0,
            "latency_ms": None,
            "output_source": str(response.get("backend") or "orchestrator").strip() or "orchestrator",
        }
    get_core_services().task_execution_service.persist_task_proposal_result(
        tid=task["id"],
        task=task,
        reason=str(response.get("reason") or ""),
        raw=str(response.get("raw") or ""),
        backend=(str(response.get("backend") or "").strip() or None),
        model=(str(response.get("model") or "").strip() or None),
        routing=response.get("routing") if isinstance(response.get("routing"), dict) else None,
        cli_result=cli_result,
        worker_context=response.get("worker_context") if isinstance(response.get("worker_context"), dict) else None,
        trace=response_trace,
        review=response.get("review") if isinstance(response.get("review"), dict) else None,
        pipeline=response.get("pipeline") if isinstance(response.get("pipeline"), dict) else None,
        command=(str(response.get("command") or "").strip() or None),
        tool_calls=response.get("tool_calls") if isinstance(response.get("tool_calls"), list) else None,
        comparisons=response.get("comparisons") if isinstance(response.get("comparisons"), dict) else None,
        research_artifact=(
            response.get("research_artifact")
            if isinstance(response.get("research_artifact"), dict)
            else None
        ),
        research_context=(
            response.get("research_context")
            if isinstance(response.get("research_context"), dict)
            else None
        ),
        forwarded_request=forwarded_request,
        history_event={
            "event_type": "proposal_result",
            "reason": str(response.get("reason") or ""),
            "backend": response.get("backend"),
            "routing_reason": (
                (response.get("routing") or {}).get("reason")
                if isinstance(response.get("routing"), dict)
                else None
            ),
            "forwarded_request": forwarded_request,
            "forwarded": True,
            "timestamp": time.time(),
        },
    )
    worker_result_service.accept_proposal_response(
        task_id=str(task["id"]),
        response=response,
    )


def persist_forwarded_execution(
    *,
    tid: str,
    response: dict,
    task: dict,
    request_data,
    last_proposal: dict | None = None,
) -> None:
    if accept_bound_forwarded_vector_index_result(
        job_id=tid,
        response=response,
        task=task,
        load_task=get_repository_registry().task_repo.get_by_id,
        classify_task=is_authoritative_vector_index_task,
        extract_result=vector_index_result_candidate,
        accept_result=accept_forwarded_vector_index_result,
    ):
        return
    vector_index_result = None
    if "status" not in response:
        return
    from agent.services.recovery_task_mutation_policy import (
        recovery_task_role,
    )

    recovery_child = recovery_task_role(task) == "child"
    authoritative_recovery_task = None
    if recovery_child:
        authoritative_recovery_task = (
            get_repository_registry().task_repo.get_by_id(tid)
        )
        if authoritative_recovery_task is None:
            raise RuntimeError("recovery_result_task_missing")
        history = list(
            getattr(authoritative_recovery_task, "history", None)
            or []
        )
        proposal_meta = dict(
            getattr(
                authoritative_recovery_task,
                "last_proposal",
                None,
            )
            or {}
        )
        verification_status = dict(
            getattr(
                authoritative_recovery_task,
                "verification_status",
                None,
            )
            or {}
        )
    else:
        history = list(task.get("history", []) or [])
        proposal_meta = dict(task.get("last_proposal", {}) or {})
        verification_status = dict(
            task.get("verification_status") or {}
        )
    raw_artifacts = response.get("artifacts")
    artifacts = (
        normalize_recovery_forwarded_artifacts(
            task_id=tid,
            artifacts=raw_artifacts,
        )
        if recovery_child
        else normalize_forwarded_artifacts(
            task_id=tid,
            artifacts=(
                list(raw_artifacts)
                if isinstance(raw_artifacts, list)
                else None
            ),
        )
    )
    from agent.services.recovery_worker_result_service import (
        get_recovery_worker_result_service,
    )

    if recovery_child:
        verification_status = (
            get_recovery_worker_result_service().merge_response(
                task_id=tid,
                phase="execute",
                response=response,
                verification_status=verification_status,
            )
        )
    elif response.get("recovery_worker_result") is not None:
        raise ValueError("recovery_worker_result_unexpected")
    execution_scope = response.get("execution_scope") if isinstance(response.get("execution_scope"), dict) else None
    execution_provenance = (
        response.get("execution_provenance") if isinstance(response.get("execution_provenance"), dict) else None
    )
    review = response.get("review") if isinstance(response.get("review"), dict) else None
    assistant_request = _accept_visual_process_assistant_result(
        tid=tid,
        response=response,
        task=task,
    )
    if assistant_request is not None:
        verification_status["visual_process_assistant_request"] = assistant_request
    from agent.services.unsloth_worker_result_service import (
        get_unsloth_worker_result_projector,
    )

    unsloth_completion_outbox_task_id = None
    unsloth_projection = (
        get_unsloth_worker_result_projector().project(
            task_id=tid,
            task=task,
            response=response,
        )
    )
    if unsloth_projection is not None:
        unsloth_completion_outbox_task_id = (
            str(
                unsloth_projection.pop(
                    (
                        "_unsloth_completion_"
                        "outbox_task_id"
                    ),
                    "",
                )
                or ""
            ).strip()
            or None
        )
        verification_status.update(unsloth_projection)
    knowledge_index_result_schema = str(response.get("schema") or "")
    if knowledge_index_result_schema in {
        "ananta.knowledge_index_job_result.v1",
        "ananta.knowledge_index_execution_result.v2",
    }:
        result_fields = {
            "schema",
            "job_id",
            "idempotency_fingerprint",
            "status",
            "reason_code",
            "knowledge_index",
            "run",
            "results",
            "artifact_refs",
            "error",
        }
        framework_fields = {"handler_contract"}
        if (
            knowledge_index_result_schema
            == "ananta.knowledge_index_execution_result.v2"
        ):
            candidate = {
                field: value
                for field, value in response.items()
                if field not in framework_fields
            }
        else:
            unknown_fields = set(response) - result_fields - framework_fields
            if unknown_fields:
                raise ValueError(
                    "knowledge_index_result_forwarding_fields_unknown"
                )
            candidate = {
                field: response.get(field) for field in result_fields
            }
        execution_context = dict(
            task.get("worker_execution_context") or {}
        )
        execution_job = dict(
            execution_context.get("knowledge_index_job") or {}
        )
        if (
            execution_job.get("schema")
            == "ananta.knowledge_index_execution_job.v2"
        ):
            job_service = _governed_source_control_index_job_service()
            assigned_worker_url = str(
                task.get("assigned_agent_url") or ""
            ).strip()
            assigned_worker = (
                get_repository_registry().agent_repo.get_by_url(
                    assigned_worker_url
                )
                if assigned_worker_url
                else None
            )
            authenticated_worker_id = str(
                getattr(assigned_worker, "name", "") or ""
            ).strip()
            if not authenticated_worker_id:
                raise ValueError(
                    "knowledge_index_result_worker_identity_missing"
                )
        else:
            job_service = get_core_services().knowledge_index_job_service
            authenticated_worker_id = None
        normalized_result = job_service.materialize_worker_result(
            job_id=tid,
            result=candidate,
            task=task,
            authenticated_worker_id=authenticated_worker_id,
        )
        verification_status["knowledge_index_job_result"] = normalized_result
    if str(response.get("schema") or "") == "ananta.mail_task_result.v1":
        result_fields = {
            "schema",
            "job_id",
            "idempotency_key",
            "operation",
            "status",
            "reason_code",
            "retryable",
            "retry_after_ms",
            "provider",
            "result_refs",
            "counters",
            "lease_fencing_token",
        }
        framework_fields = {"handler_contract"}
        unknown_fields = set(response) - result_fields - framework_fields
        if unknown_fields:
            raise ValueError("mail_task_result_forwarding_fields_unknown")
        candidate = {field: response.get(field) for field in result_fields}
        from agent.services.mail_task_service import get_mail_task_service

        normalized_result = get_mail_task_service().validate_worker_result(
            job_id=tid,
            result=candidate,
        )
        verification_status["mail_task_result"] = normalized_result
        if not recovery_child:
            get_mail_task_service().release_lease(
                job_id=tid,
                fencing_token=int(
                    normalized_result["lease_fencing_token"]
                ),
            )
    if execution_scope:
        verification_status["execution_scope"] = dict(execution_scope)
    if execution_provenance:
        verification_status["execution_provenance"] = dict(execution_provenance)
    if artifacts is not None:
        verification_status["execution_artifacts"] = artifacts
    if review:
        verification_status["execution_review"] = dict(review)
    workflow_verification = response.get("workflow_adapter_verification")
    if isinstance(workflow_verification, dict):
        adapter_result = workflow_verification.get("workflow_adapter_task_result")
        if isinstance(adapter_result, dict):
            verification_status["workflow_adapter_task_result"] = dict(adapter_result)
            nested_result = adapter_result.get("adapter_result")
            native_verification = (
                nested_result.get("verification")
                if isinstance(nested_result, dict)
                else None
            )
            if isinstance(native_verification, dict) and isinstance(
                native_verification.get("native_node_result"), dict
            ):
                verification_status["native_node_result"] = dict(
                    native_verification["native_node_result"]
                )
    history.append(
        {
            "event_type": "execution_result",
            "status": response.get("status"),
            "prompt": task.get("description"),
            "reason": "Forwarded to " + str(task.get("assigned_agent_url")),
            "command": request_data.command
            or proposal_meta.get("command"),
            "output": response.get("output"),
            "exit_code": response.get("exit_code"),
            "backend": proposal_meta.get("backend"),
            "routing_reason": ((proposal_meta.get("routing") or {}).get("reason")),
            "artifacts": artifacts,
            "execution_scope": execution_scope,
            "execution_provenance": execution_provenance,
            "review": review,
            "forwarded": True,
            "timestamp": time.time(),
        }
    )
    update_values = {
        "history": history,
        "last_output": response.get("output"),
        "last_exit_code": response.get("exit_code"),
        "verification_status": verification_status,
    }
    if isinstance(last_proposal, dict):
        update_values["last_proposal"] = dict(last_proposal)
    _persist_forwarded_execution_status(
        job_id=tid,
        response=response,
        status_values=update_values,
        recovery_child=recovery_child,
        authoritative_recovery_task=authoritative_recovery_task,
        vector_index_result=vector_index_result,
        accept_vector_result=accept_forwarded_vector_index_result,
        update_task_status=update_local_task_status,
    )
    if unsloth_completion_outbox_task_id is not None:
        from agent.services.unsloth_completion_outbox_service import (
            get_unsloth_completion_outbox_reconciler,
        )

        if not get_unsloth_completion_outbox_reconciler(
        ).reconcile_task(
            unsloth_completion_outbox_task_id
        ):
            raise RuntimeError(
                "unsloth_completion_outbox_reconciliation_failed"
            )
    if recovery_child:
        from agent.services.recovery_hub_run_evidence_service import (
            get_recovery_hub_run_evidence_service,
        )

        get_recovery_hub_run_evidence_service().accept_worker_result(
            task_id=tid,
            response=response,
            request_data=request_data,
            repositories=get_repository_registry(),
        )
    from agent.services.recovery_result_verification_service import (
        get_recovery_result_verification_service,
    )

    verification_result = (
        get_recovery_result_verification_service().verify_and_record(
            task_id=tid,
            response=response,
            artifacts=artifacts,
            publish_failure_status=False,
        )
    )
    if not recovery_child:
        return
    if not isinstance(verification_result, dict):
        raise RuntimeError("recovery_result_verification_missing")

    latest = get_repository_registry().task_repo.get_by_id(tid)
    if latest is None:
        raise RuntimeError("recovery_result_task_missing")
    final_status = (
        "completed"
        if str(verification_result.get("status") or "")
        .strip()
        .lower()
        == "passed"
        else "verification_failed"
    )
    from agent.services.recovery_dispatch_gate_service import (
        build_recovery_result_candidate,
    )

    detached = (
        latest.model_copy(deep=True)
        if callable(getattr(latest, "model_copy", None))
        else copy.deepcopy(latest)
    )
    details = dict(
        getattr(detached, "status_reason_details", None) or {}
    )
    lease = dict(details.get("recovery_dispatch_lease") or {})
    details["recovery_result_candidate"] = (
        build_recovery_result_candidate(
            task_id=tid,
            status=final_status,
            verification_record_id=str(
                verification_result.get("record_id") or ""
            ),
            lease_revision=int(lease.get("revision") or 0),
            lease_token_digest=str(
                lease.get("token_digest") or ""
            ),
            request_fingerprint=str(
                lease.get("request_fingerprint") or ""
            ),
        )
    )
    detached.status_reason_details = details
    if hasattr(detached, "updated_at"):
        detached.updated_at = time.time()
    get_repository_registry().task_repo.save(detached)


def normalize_forwarded_artifacts(*, task_id: str, artifacts: list[dict] | None) -> list[dict] | None:
    if artifacts is None:
        return None
    normalized: list[dict] = []
    for idx, item in enumerate(artifacts, start=1):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        artifact_id = str(row.get("artifact_id") or row.get("id") or "").strip()
        kind = str(row.get("kind") or "").strip()
        path = str(row.get("path") or row.get("name") or row.get("filename") or row.get("title") or "").strip()
        if not artifact_id:
            artifact_id = f"{task_id}-artifact-{idx:03d}"
        if not kind:
            kind = "task_output"
        row["artifact_id"] = artifact_id
        row.setdefault("id", artifact_id)
        row["kind"] = kind
        if path:
            row["path"] = path
        row.setdefault("task_id", task_id)
        normalized.append(row)
    return normalized


def normalize_recovery_forwarded_artifacts(
    *,
    task_id: str,
    artifacts: object,
) -> list[dict] | None:
    """Reject unbounded or open Worker artifact claims before any Hub write."""

    if artifacts is None:
        return None
    from ananta_contracts.recovery_artifact_ingress import (
        RecoveryArtifactIngressContractError,
        validate_recovery_artifact_receipt_list,
    )

    try:
        receipts = validate_recovery_artifact_receipt_list(
            artifacts,
            task_id=task_id,
        )
    except RecoveryArtifactIngressContractError as exc:
        raise ValueError(exc.reason_code) from exc
    return normalize_forwarded_artifacts(
        task_id=task_id,
        artifacts=receipts,
    )
