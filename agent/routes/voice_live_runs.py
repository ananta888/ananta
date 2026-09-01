from __future__ import annotations

import time
import uuid
from typing import Any, Mapping

from flask import Blueprint, request
from werkzeug.exceptions import RequestEntityTooLarge

from agent.auth import check_auth
from agent.common.audit import log_audit
from agent.common.errors import api_response
from agent.routes.voice_http_adapter import (
    enforce_voice_policy as _enforce_voice_policy,
)
from agent.routes.voice_http_adapter import (
    execute_hub_voice_request as _execute_hub_voice_request,
)
from agent.routes.voice_http_adapter import (
    governance_error as _governance_error,
)
from agent.routes.voice_http_adapter import (
    max_audio_mb as _max_audio_mb,
)
from agent.routes.voice_http_adapter import (
    observe as _observe,
)
from agent.routes.voice_http_adapter import (
    principal as _principal,
)
from agent.routes.voice_http_adapter import (
    provider_error as _provider_error,
)
from agent.routes.voice_http_adapter import (
    read_audio_field as _read_audio_field,
)
from agent.services.voice_governance_domain import VoiceGovernanceError
from agent.services.voice_live_run_correction_service import (
    VoiceLiveCorrectionPreparation,
    get_voice_live_run_correction_service,
)
from agent.services.voice_live_run_service import (
    VoiceLiveRunError,
    get_voice_live_run_service,
)
from agent.services.voice_live_run_start_lease_service import (
    get_voice_live_run_start_lease_service,
)
from agent.services.voice_provider import VoiceProviderError, get_voice_provider_service

voice_live_runs_bp = Blueprint("voice_live_runs", __name__)
_MULTIPART_OVERHEAD_BYTES = 256 * 1024


@voice_live_runs_bp.before_request
def _bound_live_run_request_body() -> None:
    if request.mimetype == "multipart/form-data":
        request.max_content_length = _max_audio_mb() * 1024 * 1024 + _MULTIPART_OVERHEAD_BYTES
        request.max_form_memory_size = 256 * 1024
        request.max_form_parts = 24
    else:
        request.max_content_length = 64 * 1024


@voice_live_runs_bp.errorhandler(RequestEntityTooLarge)
def _live_run_request_too_large(_exc: RequestEntityTooLarge):
    return api_response(
        status="error",
        code=413,
        data={
            "error": {
                "code": "validation.file_too_large",
                "message": f"voice segment exceeds {_max_audio_mb()}MB audio limit",
                "retriable": False,
            }
        },
    )


@voice_live_runs_bp.route("/v1/voice/live-runs", methods=["POST"])
@_observe("live_run")
@check_auth
def create_voice_live_run():
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    body = _json_body()
    try:
        snapshot, replayed = get_voice_live_run_service().create(
            _principal(),
            idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
            lease_token=str(body.get("lease_token") or ""),
            source=str(body.get("source") or "microphone"),
            profile_id=str(body.get("profile_id") or "default"),
            configuration_session_id=(str(body.get("configuration_session_id") or "").strip() or None),
            language=str(body.get("language") or "").strip() or None,
            segment_duration_seconds=body.get("segment_duration_seconds", 60),
            max_duration_seconds=body.get("max_duration_seconds", 28_800),
            overlap_milliseconds=body.get("overlap_milliseconds", 1_000),
        )
    except VoiceLiveRunError as exc:
        return _live_run_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    log_audit(
        "voice_live_run_created",
        {
            "actor": _principal().subject,
            "tenant_id": _principal().tenant_id,
            "run_id": snapshot["run"]["id"],
            "parent_task_id": snapshot["run"]["parent_task_id"],
            "max_duration_seconds": snapshot["run"]["max_duration_seconds"],
            "idempotent_replay": replayed,
            "raw_audio_stored": False,
        },
    )
    return api_response(
        data={**snapshot, "idempotent_replay": replayed},
        code=200 if replayed else 201,
    )


@voice_live_runs_bp.route("/v1/voice/live-runs/lease", methods=["POST"])
@_observe("live_run_lease")
@check_auth
def issue_voice_live_run_start_lease():
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    body = _json_body()
    try:
        lease = get_voice_live_run_start_lease_service().issue(
            _principal(),
            str(body.get("profile_id") or "default"),
        )
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
    log_audit(
        "voice_live_run_start_lease_issued",
        {
            "actor": _principal().subject,
            "tenant_id": _principal().tenant_id,
            "profile_id": lease.profile_id,
            "expires_at": lease.expires_at,
        },
    )
    return api_response(
        data={
            "lease_token": lease.lease_token,
            "expires_at": lease.expires_at,
            "profile_id": lease.profile_id,
        }
    )


@voice_live_runs_bp.route("/v1/voice/live-runs/<run_id>", methods=["GET"])
@_observe("live_run")
@check_auth
def get_voice_live_run(run_id: str):
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    try:
        after_sequence = int(request.args.get("after_sequence", -1))
        after_revision_value = request.args.get("after_revision")
        after_revision = (
            int(after_revision_value) if after_revision_value is not None else None
        )
        limit = int(request.args.get("limit", 600))
        include_text = str(request.args.get("include_text", "true")).strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        snapshot = get_voice_live_run_service().snapshot(
            _principal(),
            run_id,
            after_sequence=after_sequence,
            after_revision=after_revision,
            limit=limit,
            include_text=include_text,
        )
        get_voice_live_run_correction_service().schedule_pending(
            _principal(),
            run_id,
        )
        return api_response(data=snapshot)
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=422,
            data={
                "error": {
                    "code": "voice_live_run.invalid_cursor",
                    "message": "after_sequence, after_revision and limit must be integers",
                    "retriable": False,
                }
            },
        )
    except VoiceLiveRunError as exc:
        return _live_run_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)


@voice_live_runs_bp.route("/v1/voice/live-runs/<run_id>/heartbeat", methods=["POST"])
@_observe("live_run")
@check_auth
def heartbeat_voice_live_run(run_id: str):
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    body = _json_body()
    try:
        get_voice_live_run_correction_service().schedule_pending(
            _principal(),
            run_id,
        )
        snapshot = get_voice_live_run_service().heartbeat(
            _principal(),
            run_id,
            last_local_sequence=body.get("last_local_sequence"),
            gaps=body.get("gaps", []),
        )
        return api_response(data=snapshot)
    except VoiceLiveRunError as exc:
        return _live_run_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)


@voice_live_runs_bp.route(
    "/v1/voice/live-runs/<run_id>/segments/<int:sequence>",
    methods=["PUT"],
)
@_observe("live_run_segment")
@check_auth
def put_voice_live_run_segment(run_id: str, sequence: int):
    blocked, _policy = _enforce_voice_policy("transcribe")
    if blocked:
        return blocked
    if request.mimetype == "multipart/form-data":
        return _put_audio_segment(run_id, sequence)
    return _put_result_reference_segment(run_id, sequence)


def _put_audio_segment(run_id: str, sequence: int):
    (filename, payload), error = _read_audio_field("file")
    if error:
        return error
    principal = _principal()
    service = get_voice_live_run_service()
    caller_idempotency_key = str(request.headers.get("Idempotency-Key") or "").strip()
    try:
        claim = service.reserve_audio_segment(
            principal,
            run_id,
            sequence=sequence,
            idempotency_key=caller_idempotency_key,
            audio=payload,
            started_at_ms=request.form.get("started_at_ms"),
            ended_at_ms=request.form.get("ended_at_ms"),
            duration_ms=request.form.get("duration_ms"),
            overlap_milliseconds=request.form.get(
                "overlap_milliseconds",
                0,
            ),
        )
        if claim.reservation.replayed:
            snapshot = service.snapshot(
                principal,
                run_id,
                after_revision=max(-1, claim.reservation.segment.timeline_revision - 1),
                limit=1,
                include_text=True,
            )
            get_voice_live_run_correction_service().schedule(
                principal,
                run_id,
                sequence,
            )
            return api_response(
                data={
                    **snapshot,
                    "segment": _segment_from_snapshot(snapshot, sequence),
                    "idempotent_replay": True,
                }
            )
        provider = get_voice_provider_service()
        audit_id = f"audit-voice-live-segment-{uuid.uuid4().hex}"
        execution_task_id: str | None = None

        def bind_execution_task(delegation) -> None:
            nonlocal execution_task_id
            execution_task_id = delegation.task_id
            service.bind_segment_task(
                principal,
                run_id,
                sequence=sequence,
                idempotency_key_digest=claim.idempotency_key_digest,
                attempt_count=claim.reservation.segment.attempt_count,
                task_id=delegation.task_id,
            )

        def fence_execution() -> None:
            service.assert_segment_execution_allowed(
                principal,
                run_id,
                profile_id=claim.run.profile_id,
                run_created_at=claim.run.created_at,
                sequence=sequence,
                idempotency_key_digest=claim.idempotency_key_digest,
                attempt_count=claim.reservation.segment.attempt_count,
                expected_task_id=execution_task_id,
            )

        execution = _execute_hub_voice_request(
            operation="live_segment",
            principal=principal,
            filename=filename,
            payload=payload,
            profile_id=claim.run.profile_id,
            configuration_session_id=claim.run.configuration_session_id,
            idempotency_key=claim.effective_idempotency_key,
            idempotency_payload={
                "live_run_id": claim.run.id,
                "segment_sequence": sequence,
                "started_at_ms": claim.reservation.segment.started_at_ms,
                "ended_at_ms": claim.reservation.segment.ended_at_ms,
                "duration_ms": claim.reservation.segment.duration_ms,
                "attempt_count": claim.reservation.segment.attempt_count,
            },
            audit_id=audit_id,
            request_started_epoch_ms=time.time_ns() // 1_000_000,
            parent_task_id=claim.run.parent_task_id,
            invoke=lambda remaining, context: provider.transcribe(
                content=payload,
                filename=filename,
                language=claim.run.language,
                recognition_context=context,
                request_id=audit_id,
                deadline_seconds=remaining,
            ),
            on_delegated=bind_execution_task,
            completion_fence=fence_execution,
            on_execution_error=lambda failure: service.compensate_failed_execution_if_unowned(
                principal,
                run_id,
                profile_id=claim.run.profile_id,
                run_created_at=claim.run.created_at,
                sequence=sequence,
                idempotency_key_digest=claim.idempotency_key_digest,
                attempt_count=claim.reservation.segment.attempt_count,
                request_ref=failure.request_ref,
                task_id=failure.task_id,
                result_ref=failure.result_ref,
                idempotency_service=failure.idempotency,
                idempotency_claim=failure.claim,
            ),
        )
        correction_service = get_voice_live_run_correction_service()
        try:
            preparation = correction_service.prepare(
                principal,
                claim.run,
                sequence=sequence,
                provisional_result_ref=execution.result_ref,
                effective_configuration=execution.effective_configuration,
            )
        except Exception:
            # Correction is optional; encrypted ASR remains authoritative when
            # a correction spec cannot be prepared.
            preparation = VoiceLiveCorrectionPreparation(requested=False)
        try:
            published = service.publish_provisional(
                principal,
                run_id,
                sequence=sequence,
                idempotency_key_digest=claim.idempotency_key_digest,
                attempt_count=claim.reservation.segment.attempt_count,
                task_id=execution.task_id,
                result_ref=execution.result_ref,
                correction_configuration_digest=preparation.configuration_digest,
                correction_spec_ref=preparation.spec_ref,
                correction_requested=preparation.requested,
            )
        except VoiceLiveRunError as exc:
            correction_service.discard_preparation_if_unowned(
                principal,
                run_id,
                sequence,
                preparation,
            )
            if service.compensate_completed_execution_if_unowned(
                principal,
                run_id,
                profile_id=claim.run.profile_id,
                run_created_at=claim.run.created_at,
                sequence=sequence,
                idempotency_key_digest=claim.idempotency_key_digest,
                attempt_count=claim.reservation.segment.attempt_count,
                task_id=execution.task_id,
                result_ref=execution.result_ref,
                idempotency_service=execution.idempotency,
                idempotency_claim=execution.claim,
            ):
                raise VoiceLiveRunError(
                    "voice_live_run.execution_no_longer_owned",
                    "voice live segment was stopped, expired, or deleted while processing",
                    409,
                ) from exc
            if exc.code == "voice_live_run.not_found":
                service.discard_orphaned_execution(
                    principal,
                    profile_id=claim.run.profile_id,
                    task_id=execution.task_id,
                    result_ref=execution.result_ref,
                    idempotency_service=execution.idempotency,
                    idempotency_claim=execution.claim,
                )
                raise VoiceLiveRunError(
                    "voice_live_run.deleted_during_processing",
                    "voice live run was deleted while the segment was processing",
                    409,
                ) from exc
            raise
        snapshot = service.snapshot(
            principal,
            run_id,
            after_revision=max(-1, published.timeline_revision - 1),
            limit=1,
            include_text=True,
        )
        log_audit(
            "voice_live_run_segment_provisional_published",
            {
                "actor": principal.subject,
                "tenant_id": principal.tenant_id,
                "run_id": run_id,
                "segment_sequence": sequence,
                "task_id": execution.task_id,
                "result_ref": execution.result_ref,
                "audio_size_bytes": len(payload),
                "raw_audio_stored": False,
            },
        )
        # Finish every request-owned database write before the asynchronous
        # correction worker opens its own transaction. This is required for
        # SQLite test deployments and also keeps the publication audit ordered
        # before any correction-side effects in production.
        if preparation.requested:
            correction_service.schedule(principal, run_id, sequence)
        return api_response(
            data={
                **snapshot,
                "segment": _segment_from_snapshot(snapshot, sequence),
                "result": execution.result,
                "result_ref": execution.result_ref,
                "result_digest": execution.result_digest,
                "idempotent_replay": execution.idempotent_replay,
            }
        )
    except VoiceProviderError as exc:
        if "claim" in locals():
            service.fail_segment(
                principal,
                run_id,
                sequence=sequence,
                idempotency_key_digest=claim.idempotency_key_digest,
                attempt_count=claim.reservation.segment.attempt_count,
                failure_code=service.failure_code(exc),
            )
            service.discard_unbound_tasks_if_run_deleted(
                principal,
                run_id,
                profile_id=claim.run.profile_id,
                parent_task_id=claim.run.parent_task_id,
            )
        return _provider_error(exc)
    except VoiceLiveRunError as exc:
        if "claim" in locals():
            service.discard_unbound_tasks_if_run_deleted(
                principal,
                run_id,
                profile_id=claim.run.profile_id,
                parent_task_id=claim.run.parent_task_id,
            )
        return _live_run_error(exc)
    except VoiceGovernanceError as exc:
        if "claim" in locals():
            service.fail_segment(
                principal,
                run_id,
                sequence=sequence,
                idempotency_key_digest=claim.idempotency_key_digest,
                attempt_count=claim.reservation.segment.attempt_count,
                failure_code=service.failure_code(exc),
            )
            service.discard_unbound_tasks_if_run_deleted(
                principal,
                run_id,
                profile_id=claim.run.profile_id,
                parent_task_id=claim.run.parent_task_id,
            )
        return _governance_error(exc)
    except Exception as exc:
        if "claim" in locals():
            service.fail_segment(
                principal,
                run_id,
                sequence=sequence,
                idempotency_key_digest=claim.idempotency_key_digest,
                attempt_count=claim.reservation.segment.attempt_count,
                failure_code=service.failure_code(exc),
            )
            service.discard_unbound_tasks_if_run_deleted(
                principal,
                run_id,
                profile_id=claim.run.profile_id,
                parent_task_id=claim.run.parent_task_id,
            )
        raise


def _put_result_reference_segment(run_id: str, sequence: int):
    body = _json_body()
    try:
        snapshot = get_voice_live_run_service().register_result_segment(
            _principal(),
            run_id,
            sequence=sequence,
            idempotency_key=str(request.headers.get("Idempotency-Key") or ""),
            result_ref=str(body.get("result_ref") or ""),
            started_at_ms=body.get("started_at_ms"),
            ended_at_ms=body.get("ended_at_ms"),
            duration_ms=body.get("duration_ms"),
            overlap_milliseconds=body.get("overlap_milliseconds", 0),
        )
        return api_response(
            data={
                **snapshot,
                "segment": _segment_from_snapshot(snapshot, sequence),
                "idempotent_replay": False,
            }
        )
    except VoiceLiveRunError as exc:
        return _live_run_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)


@voice_live_runs_bp.route("/v1/voice/live-runs/<run_id>/stop", methods=["POST"])
@_observe("live_run")
@check_auth
def stop_voice_live_run(run_id: str):
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    body = _json_body()
    try:
        snapshot = get_voice_live_run_service().stop(
            _principal(),
            run_id,
            last_sequence=body.get("last_sequence"),
            reason=str(body.get("reason") or "user_stop"),
        )
        return api_response(data=snapshot)
    except VoiceLiveRunError as exc:
        return _live_run_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)


def _segment_from_snapshot(snapshot: Mapping[str, Any], sequence: int) -> dict[str, Any] | None:
    return next(
        (dict(item) for item in list(snapshot.get("segments") or []) if int(item.get("sequence", -1)) == sequence),
        None,
    )


def _json_body() -> dict[str, Any]:
    value = request.get_json(silent=True)
    return dict(value) if isinstance(value, Mapping) else {}


def _live_run_error(exc: VoiceLiveRunError):
    return api_response(
        status="error",
        code=exc.status_code,
        data={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retriable": exc.retriable,
            }
        },
    )
