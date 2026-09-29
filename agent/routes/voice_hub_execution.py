"""Artifact-first Hub voice execution, crash recovery and deferred completion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Mapping,
)

from agent.routes.voice_recognition_context import _hub_effective_configuration
from agent.routes.voice_request_deadlines import (
    _context_with_remaining_deadline,
    _deadline_epoch_ms,
)
from agent.routes.voice_request_support import (
    _deadline_seconds,
    _voice_request_ref,
)
from agent.services.voice_admission_service import (
    VoiceAdmissionLease,
    estimate_batch_audio_seconds,
)
from agent.services.voice_delegation_task_service import (
    VoiceDelegationTask,
    get_voice_delegation_task_service,
)
from agent.services.voice_governance_domain import (
    VoicePrincipal,
    voice_idempotency_audio_binding,
)
from agent.services.voice_idempotency_service import (
    VoiceIdempotencyClaim,
    VoiceIdempotencyService,
)
from agent.services.voice_provider import VoiceProviderError

if TYPE_CHECKING:
    # Type-only: the bundle references this module's executor, so it is passed in, not imported.
    from agent.routes.voice_route_dependencies import VoiceRouteDependencies


@dataclass(frozen=True)
class _HubVoiceExecution:
    result: dict[str, Any]
    result_ref: str
    result_digest: str
    task_id: str
    idempotent_replay: bool
    idempotency: VoiceIdempotencyService
    claim: VoiceIdempotencyClaim | None
    delegation: VoiceDelegationTask | None
    effective_configuration: dict[str, Any]


@dataclass(frozen=True)
class _HubVoiceFailureContext:
    """Opaque handles needed by an owning workflow to compensate a race."""

    request_ref: str
    result_ref: str | None
    task_id: str | None
    idempotency: VoiceIdempotencyService
    claim: VoiceIdempotencyClaim | None


def _recover_hub_voice_execution(
    *,
    dependencies: VoiceRouteDependencies,
    operation: str,
    principal: VoicePrincipal,
    request_ref: str,
    profile_id: str,
    configuration_session_id: str | None,
    idempotency_key: str,
    idempotency: VoiceIdempotencyService,
    claim: VoiceIdempotencyClaim,
    audit_id: str,
    effective_configuration: Mapping[str, Any],
    deadline_budget: float,
    deadline_epoch_ms: int,
    defer_completion: bool,
    parent_task_id: str | None = None,
    completion_fence: Callable[[], None] | None = None,
    on_delegated: Callable[[VoiceDelegationTask], None] | None = None,
    on_artifact: Callable[[str], None] | None = None,
) -> _HubVoiceExecution | None:
    """Recover an artifact-first Voice request without invoking its provider."""

    if claim.replayed:
        return None
    if completion_fence is not None:
        completion_fence()
    artifact = dependencies.get_voice_result_artifact_service().find_live_envelope(
        principal,
        request_ref=request_ref,
        profile_id=profile_id,
    )
    if artifact is None:
        return None
    if on_artifact is not None:
        on_artifact(str(artifact["id"]))
    if completion_fence is not None:
        completion_fence()
    delegation_service = get_voice_delegation_task_service()
    delegation = delegation_service.start(
        principal,
        request_id=audit_id,
        request_hash=request_ref,
        effective_configuration=dict(effective_configuration),
        deadline_seconds=deadline_budget,
        idempotency_key=idempotency_key,
        deadline_epoch_ms=deadline_epoch_ms,
        profile_id=profile_id,
        configuration_session_id=configuration_session_id,
        parent_task_id=parent_task_id,
        operation=operation,
    )
    if on_delegated is not None:
        on_delegated(delegation)
    if completion_fence is not None:
        completion_fence()
    if not defer_completion:
        delegation_service.complete(delegation, result_ref=str(artifact["id"]))
        if completion_fence is not None:
            completion_fence()
        idempotency.complete(
            claim,
            {"result_ref": artifact["id"], "task_id": delegation.task_id},
        )
        if completion_fence is not None:
            completion_fence()
    return _HubVoiceExecution(
        result=dict(artifact["result"]),
        result_ref=str(artifact["id"]),
        result_digest=str(artifact["payload_digest"]),
        task_id=delegation.task_id,
        idempotent_replay=True,
        idempotency=idempotency,
        claim=claim,
        delegation=delegation,
        effective_configuration=dict(effective_configuration),
    )


def _execute_hub_voice_request(
    *,
    dependencies: VoiceRouteDependencies,
    operation: str,
    principal: VoicePrincipal,
    filename: str,
    payload: bytes,
    profile_id: str,
    configuration_session_id: str | None,
    idempotency_key: str,
    idempotency_payload: dict[str, Any],
    audit_id: str,
    request_started_epoch_ms: int,
    invoke: Callable[[float, dict | None], Mapping[str, Any]],
    parent_task_id: str | None = None,
    transform_result: Callable[
        [Mapping[str, Any], Mapping[str, Any], VoiceDelegationTask],
        Mapping[str, Any],
    ]
    | None = None,
    on_delegated: Callable[[VoiceDelegationTask], None] | None = None,
    completion_fence: Callable[[], None] | None = None,
    on_execution_error: Callable[[_HubVoiceFailureContext], bool] | None = None,
    defer_completion: bool = False,
) -> _HubVoiceExecution:
    """Run one bounded provider call through Hub admission and task ownership."""

    idempotency = VoiceIdempotencyService()
    claim: VoiceIdempotencyClaim | None = None
    delegation: VoiceDelegationTask | None = None
    admission_lease: VoiceAdmissionLease | None = None
    artifact_ref: str | None = None
    admission_service = dependencies.get_voice_admission_service()
    request_hash = _voice_request_ref(
        principal,
        operation=operation,
        idempotency_key=idempotency_key,
    )
    try:
        recognition_context = dependencies.recognition_context(
            principal,
            profile_id=profile_id,
            session_id=configuration_session_id,
        )
        effective_configuration = _hub_effective_configuration(recognition_context)
        configured_deadline = (
            float(effective_configuration.get("candidate_deadline_sec") or 120.0)
            if isinstance(effective_configuration, dict)
            else 120.0
        )
        requested_deadline = _deadline_seconds()
        deadline_budget = min(
            requested_deadline if requested_deadline is not None else configured_deadline,
            configured_deadline,
        )
        absolute_deadline_epoch_ms = _deadline_epoch_ms(
            request_started_epoch_ms=request_started_epoch_ms,
            budget_seconds=deadline_budget,
        )
        if idempotency_key:
            claim = idempotency.begin(
                principal,
                operation=f"voice.{operation}",
                idempotency_key=idempotency_key,
                payload={
                    "operation": operation,
                    "audio_size_bytes": len(payload),
                    "audio_binding": voice_idempotency_audio_binding(
                        principal,
                        operation=f"voice.{operation}",
                        idempotency_key=idempotency_key,
                        audio=payload,
                    ),
                    "filename": filename,
                    "profile_id": profile_id,
                    "configuration_session_id": configuration_session_id,
                    "effective_configuration": effective_configuration,
                    **idempotency_payload,
                },
            )
            request_hash = _voice_request_ref(
                principal,
                operation=operation,
                idempotency_key=idempotency_key,
                claim_id=claim.record_id,
            )
            if claim.replayed:
                if completion_fence is not None:
                    completion_fence()
                result_ref = str(claim.result_metadata.get("result_ref") or "")
                artifact = dependencies.get_voice_result_artifact_service().get(principal, result_ref)
                return _HubVoiceExecution(
                    result=dict(artifact["result"]),
                    result_ref=result_ref,
                    result_digest=str(artifact["payload_digest"]),
                    task_id=str(claim.result_metadata.get("task_id") or ""),
                    idempotent_replay=True,
                    idempotency=idempotency,
                    claim=claim,
                    delegation=None,
                    effective_configuration=(
                        dict(effective_configuration)
                        if isinstance(effective_configuration, Mapping)
                        else {}
                    ),
                )
            if completion_fence is not None:
                completion_fence()

            def capture_recovered_delegation(value: VoiceDelegationTask) -> None:
                nonlocal delegation
                delegation = value
                if on_delegated is not None:
                    on_delegated(value)

            def capture_recovered_artifact(value: str) -> None:
                nonlocal artifact_ref
                artifact_ref = value

            recovered = _recover_hub_voice_execution(
                dependencies=dependencies,
                operation=operation,
                principal=principal,
                request_ref=request_hash,
                profile_id=profile_id,
                configuration_session_id=configuration_session_id,
                idempotency_key=idempotency_key,
                idempotency=idempotency,
                claim=claim,
                audit_id=audit_id,
                effective_configuration=(
                    effective_configuration if isinstance(effective_configuration, Mapping) else {}
                ),
                deadline_budget=deadline_budget,
                deadline_epoch_ms=absolute_deadline_epoch_ms,
                defer_completion=defer_completion,
                parent_task_id=parent_task_id,
                completion_fence=completion_fence,
                on_delegated=capture_recovered_delegation,
                on_artifact=capture_recovered_artifact,
            )
            if recovered is not None:
                return recovered

        admission_limits = dependencies.admission_limits()
        admission_lease = admission_service.acquire(
            principal,
            audio_seconds=estimate_batch_audio_seconds(
                filename=filename,
                content=payload,
                unknown_audio_seconds=admission_limits.max_audio_seconds_per_request,
            ),
            deadline_epoch_ms=absolute_deadline_epoch_ms,
            limits=admission_limits,
        )
        delegation_service = get_voice_delegation_task_service()
        delegation = delegation_service.start(
            principal,
            request_id=audit_id,
            request_hash=request_hash,
            effective_configuration=(effective_configuration if isinstance(effective_configuration, dict) else {}),
            deadline_seconds=deadline_budget,
            idempotency_key=idempotency_key or None,
            deadline_epoch_ms=absolute_deadline_epoch_ms,
            profile_id=profile_id,
            configuration_session_id=configuration_session_id,
            parent_task_id=parent_task_id,
            operation=operation,
        )
        if on_delegated is not None:
            on_delegated(delegation)
        if completion_fence is not None:
            completion_fence()
        remaining_deadline = delegation_service.remaining_seconds(delegation)
        if remaining_deadline <= 0:
            raise VoiceProviderError("voice.timeout", "voice request deadline expired", 504, True)
        result: Mapping[str, Any] = dict(
            invoke(
                remaining_deadline,
                _context_with_remaining_deadline(recognition_context, remaining_deadline),
            )
        )
        if completion_fence is not None:
            completion_fence()
        if transform_result is not None:
            result = transform_result(result, effective_configuration, delegation)
        if completion_fence is not None:
            completion_fence()
        artifact = dependencies.get_voice_result_artifact_service().create(
            principal,
            request_hash=request_hash,
            result=result,
            profile_id=profile_id,
        )
        artifact_ref = str(artifact["id"])
        if completion_fence is not None:
            completion_fence()
        if not defer_completion:
            delegation_service.complete(delegation, result_ref=artifact["id"])
            if completion_fence is not None:
                completion_fence()
            if claim is not None:
                idempotency.complete(
                    claim,
                    {"result_ref": artifact["id"], "task_id": delegation.task_id},
                )
                if completion_fence is not None:
                    completion_fence()
        return _HubVoiceExecution(
            result=result,
            result_ref=str(artifact["id"]),
            result_digest=str(artifact["payload_digest"]),
            task_id=delegation.task_id,
            idempotent_replay=False,
            idempotency=idempotency,
            claim=claim,
            delegation=delegation,
            effective_configuration=(
                dict(effective_configuration)
                if isinstance(effective_configuration, Mapping)
                else {}
            ),
        )
    except Exception as exc:
        failure_handled = False
        if on_execution_error is not None:
            failure_handled = bool(
                on_execution_error(
                    _HubVoiceFailureContext(
                        request_ref=request_hash,
                        result_ref=artifact_ref,
                        task_id=delegation.task_id if delegation is not None else None,
                        idempotency=idempotency,
                        claim=claim,
                    )
                )
            )
        if not failure_handled:
            if delegation is not None:
                get_voice_delegation_task_service().fail(delegation, exc)
            if claim is not None:
                idempotency.abandon(claim)
        raise
    finally:
        admission_service.release(admission_lease)


def _complete_deferred_hub_voice_execution(
    execution: _HubVoiceExecution,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    if execution.idempotent_replay and (execution.claim is None or execution.claim.replayed):
        return
    if execution.delegation is not None:
        get_voice_delegation_task_service().complete(
            execution.delegation,
            result_ref=execution.result_ref,
        )
    if execution.claim is not None:
        execution.idempotency.complete(
            execution.claim,
            {
                "result_ref": execution.result_ref,
                "task_id": execution.task_id,
                **dict(metadata or {}),
            },
        )


def _fail_deferred_hub_voice_execution(
    execution: _HubVoiceExecution,
    exc: BaseException,
) -> None:
    if execution.idempotent_replay and (execution.claim is None or execution.claim.replayed):
        return
    if execution.delegation is not None:
        get_voice_delegation_task_service().fail(execution.delegation, exc)
    if execution.claim is not None:
        execution.idempotency.abandon(execution.claim)
