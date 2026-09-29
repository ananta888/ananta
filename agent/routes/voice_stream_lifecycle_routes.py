"""Voice stream chunk upload, finalize, status and delete endpoints."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from flask import request

from agent.auth import check_auth
from agent.common.errors import api_response
from agent.routes.voice_blueprint import voice_bp
from agent.routes.voice_request_policy import (
    _enforce_voice_policy,
)
from agent.routes.voice_request_support import (
    _governance_error,
    _observe,
    _principal,
    _provider_error,
)
from agent.routes.voice_route_dependencies import voice_route_dependencies
from agent.services.voice_delegation_task_service import (
    VoiceDelegationTask,
    get_voice_delegation_task_service,
)
from agent.services.voice_governance_domain import (
    VoiceGovernanceError,
    VoicePrincipal,
)
from agent.services.voice_observability import (
    record_stream_event,
    record_voice_result,
)
from agent.services.voice_provider import VoiceProviderError
from agent.services.voice_runtime_cleanup_service import get_voice_runtime_cleanup_service
from agent.services.voice_stream_session_service import get_voice_stream_session_service


@voice_bp.route("/v1/voice/streams/<session_id>/chunks/<int:chunk_sequence>", methods=["PUT"])
@_observe("stream")
@check_auth
def push_voice_stream_chunk(session_id: str, chunk_sequence: int):
    dependencies = voice_route_dependencies()
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    chunk = request.stream.read((1024 * 1024) + 1)
    if not chunk or len(chunk) > 1024 * 1024:
        return api_response(
            status="error",
            code=413 if chunk else 422,
            data={"error": {"code": "voice_stream.invalid_chunk", "message": "chunk must contain at most 1MB"}},
        )
    principal = _principal()
    try:
        session_service = get_voice_stream_session_service()
        chunk_digest = hashlib.sha256(chunk).hexdigest()
        reservation = session_service.begin_chunk(
            principal,
            session_id,
            chunk_sequence=chunk_sequence,
            chunk_digest=chunk_digest,
            chunk_size=len(chunk),
        )
        session = reservation.session
        if reservation.replayed:
            replay_event = {
                "event_type": "chunk_replayed",
                "payload": {
                    "chunk_sequence": chunk_sequence,
                    "next_chunk_sequence": session.next_chunk_sequence,
                },
            }
            record_stream_event("chunk_replayed")
            return api_response(data={"stream": session.public(), "event": replay_event}, code=202)
        provider = dependencies.get_voice_provider_service()
        try:
            runtime = provider.push_stream_chunk(
                runtime_session_id=session.runtime_session_id,
                chunk_sequence=chunk_sequence,
                content=chunk,
                request_id=session.request_id,
                deadline_seconds=max(0.001, session.deadline_at - time.time()),
            )
        except Exception:
            session_service.abort_chunk(
                principal,
                session_id,
                chunk_sequence=chunk_sequence,
                chunk_digest=chunk_digest,
            )
            raise
        try:
            session = session_service.complete_chunk(
                principal,
                session_id,
                chunk_sequence=chunk_sequence,
                chunk_digest=chunk_digest,
            )
        except Exception:
            session_service.abort_chunk(
                principal,
                session_id,
                chunk_sequence=chunk_sequence,
                chunk_digest=chunk_digest,
            )
            try:
                provider.delete_stream(
                    runtime_session_id=session.runtime_session_id,
                    request_id=session.request_id,
                    deadline_seconds=max(0.001, session.deadline_at - time.time()),
                )
            except Exception:
                pass
            raise
        event_raw = runtime.get("event")
        event_value: dict[str, Any] = event_raw if isinstance(event_raw, dict) else {}
        record_stream_event(event_value.get("event_type") or "ack")
        return api_response(data={"stream": session.public(), "event": runtime.get("event")}, code=202)
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)


@voice_bp.route("/v1/voice/streams/<session_id>/finalize", methods=["POST"])
@_observe("stream")
@check_auth
def finalize_voice_stream(session_id: str):
    dependencies = voice_route_dependencies()
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    principal = _principal()
    session = None
    finalize_token = ""
    try:
        session_service = get_voice_stream_session_service()
        finalize_reservation = session_service.begin_finalize(principal, session_id)
        session = finalize_reservation.session
        finalize_token = finalize_reservation.token
        runtime = dependencies.get_voice_provider_service().finalize_stream(
            runtime_session_id=session.runtime_session_id,
            request_id=session.request_id,
            deadline_seconds=max(0.001, session.deadline_at - time.time()),
        )
        event_value = runtime.get("event")
        event: dict[str, Any] = event_value if isinstance(event_value, dict) else {}
        payload_value = event.get("payload")
        payload: dict[str, Any] = payload_value if isinstance(payload_value, dict) else {}
        result_value = payload.get("result")
        result: dict[str, Any] = result_value if isinstance(result_value, dict) else {}
        snapshot_value = json.loads(session.effective_configuration_json)
        effective_configuration = snapshot_value if isinstance(snapshot_value, dict) else {}
        feature_flags = (
            effective_configuration.get("feature_flags") if isinstance(effective_configuration, dict) else None
        )
        if (
            isinstance(effective_configuration, dict)
            and effective_configuration.get("correction_policy") == "generative_rewrite"
            and isinstance(feature_flags, dict)
            and feature_flags.get("generative_corrector") is True
        ):
            corrector_outcome = dependencies.get_voice_generative_corrector_service().apply(
                result,
                effective_configuration=effective_configuration,
                tenant_id=principal.tenant_id,
                parent_task_id=session.task_id,
                request_id=session.request_id,
                language=session.language,
                deadline_epoch_ms=round(session.deadline_at * 1000),
            )
            result = corrector_outcome.result
            payload = {**payload, "result": result}
            event = {**event, "payload": payload}
        artifact = dependencies.get_voice_result_artifact_service().create(
            principal,
            request_hash=hashlib.sha256(f"stream:{session.session_id}".encode()).hexdigest(),
            result=result,
            profile_id=session.profile_id,
        )
        session = session_service.complete_finalize(
            principal,
            session_id,
            token=finalize_token,
            result_ref=artifact["id"],
        )
        if session.task_id:
            get_voice_delegation_task_service().complete(
                VoiceDelegationTask(task_id=session.task_id, deadline_epoch_ms=0),
                result_ref=artifact["id"],
            )
        cleanup = get_voice_runtime_cleanup_service()
        cleanup.activate_target(
            principal,
            session.profile_id,
            session.session_id,
            operation="stream_orphan",
        )
        cleanup.retry_target(principal, session.profile_id, session.session_id)
        record_voice_result(result)
        record_stream_event("final")
        return api_response(
            data={"stream": session.public(), "result": result, "result_ref": artifact["id"], "event": event}
        )
    except VoiceProviderError as exc:
        if finalize_token:
            _fail_finalize_and_cleanup(principal, session_id, finalize_token)
        if session is not None and session.task_id:
            get_voice_delegation_task_service().fail(
                VoiceDelegationTask(task_id=session.task_id, deadline_epoch_ms=0),
                exc,
            )
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        if finalize_token:
            _fail_finalize_and_cleanup(principal, session_id, finalize_token)
        if session is not None and session.task_id:
            get_voice_delegation_task_service().fail(
                VoiceDelegationTask(task_id=session.task_id, deadline_epoch_ms=0),
                exc,
            )
        return _governance_error(exc)
    except Exception as exc:
        if finalize_token:
            _fail_finalize_and_cleanup(principal, session_id, finalize_token)
        if session is not None and session.task_id:
            get_voice_delegation_task_service().fail(
                VoiceDelegationTask(task_id=session.task_id, deadline_epoch_ms=0),
                exc,
            )
        raise


def _fail_finalize_and_cleanup(
    principal: VoicePrincipal,
    session_id: str,
    finalize_token: str,
) -> None:
    failed_session = get_voice_stream_session_service().fail_finalize(
        principal,
        session_id,
        token=finalize_token,
    )
    if failed_session is None:
        return
    cleanup = get_voice_runtime_cleanup_service()
    cleanup.activate_target(
        principal,
        failed_session.profile_id,
        failed_session.session_id,
        operation="stream_orphan",
    )
    cleanup.retry_target(principal, failed_session.profile_id, failed_session.session_id)


@voice_bp.route("/v1/voice/streams/<session_id>", methods=["GET"])
@_observe("stream")
@check_auth
def get_voice_stream(session_id: str):
    dependencies = voice_route_dependencies()
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    principal = _principal()
    try:
        session = get_voice_stream_session_service().require(principal, session_id)
        after_event = int(request.args.get("after_event", -1))
        runtime = dependencies.get_voice_provider_service().get_stream(
            runtime_session_id=session.runtime_session_id,
            after_event=after_event,
            request_id=session.request_id,
            deadline_seconds=max(0.001, session.deadline_at - time.time()),
        )
        return api_response(data={"stream": session.public(), "runtime": runtime})
    except (TypeError, ValueError):
        return api_response(
            status="error",
            code=400,
            data={"error": {"code": "voice_stream.invalid_cursor", "message": "after_event must be an integer"}},
        )
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)


@voice_bp.route("/v1/voice/streams/<session_id>", methods=["DELETE"])
@_observe("stream")
@check_auth
def delete_voice_stream(session_id: str):
    dependencies = voice_route_dependencies()
    blocked, _policy = _enforce_voice_policy("stream")
    if blocked:
        return blocked
    principal = _principal()
    try:
        session = get_voice_stream_session_service().require(principal, session_id)
        was_terminal = session.state in {"final", "failed", "closed"}
        cleanup = get_voice_runtime_cleanup_service()
        cleanup.activate_target(
            principal,
            session.profile_id,
            session.session_id,
            operation="stream_orphan",
        )
        dependencies.get_voice_provider_service().delete_stream(
            runtime_session_id=session.runtime_session_id,
            request_id=session.request_id,
            deadline_seconds=max(0.001, session.deadline_at - time.time()),
        )
        cleanup.cancel_target(principal, session.profile_id, session.session_id)
        session = get_voice_stream_session_service().delete(principal, session_id)
        if session.task_id and not was_terminal:
            get_voice_delegation_task_service().cancel(
                session.task_id,
                reason_code="voice_stream_cancelled",
            )
        record_stream_event("cancelled")
        return api_response(data={"stream": session.public(), "deleted": True})
    except VoiceProviderError as exc:
        return _provider_error(exc)
    except VoiceGovernanceError as exc:
        return _governance_error(exc)
