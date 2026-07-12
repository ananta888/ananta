from __future__ import annotations

import hashlib
import json
import time
import uuid

import pytest

from agent.services.restricted_inference_contract import (
    CONTRACT_VERSION,
    RestrictedInferenceOperation,
    RestrictedInferenceRequest,
    RestrictedInferenceResponse,
)
from agent.services.restricted_inference_port import HubTaskQueueRestrictedInferencePort


class _SuccessfulPort:
    def execute(self, request: RestrictedInferenceRequest) -> RestrictedInferenceResponse:
        return RestrictedInferenceResponse.from_dict(
            {
                "contract_version": CONTRACT_VERSION,
                "request_id": request.request_id,
                "task_id": request.task_id,
                "operation": request.operation.value,
                "status": "succeeded",
                "result": {
                    "label": "safe",
                    "confidence": 1.0,
                    "all_scores": {"safe": 1.0},
                    "engine": "fixture",
                    "model_id": "fixture/model",
                    "manifest_digest": "a" * 64,
                    "latency_ms": 1.0,
                },
                "error": None,
                "no_generation": True,
            }
        )


class _FailingPort:
    def execute(self, request: RestrictedInferenceRequest) -> RestrictedInferenceResponse:
        del request
        raise TimeoutError("sensitive worker failure details")


def _request(*, secret: str) -> RestrictedInferenceRequest:
    suffix = uuid.uuid4().hex
    return RestrictedInferenceRequest(
        request_id=f"request-{suffix}",
        task_id=f"parent-{suffix}",
        run_id=f"run-{suffix}",
        tenant_id=f"tenant-{suffix}",
        operation=RestrictedInferenceOperation.CLASSIFY,
        payload={"text": secret, "labels": ["safe"]},
        model_manifest_id="fixture-manifest-v1",
        policy_hash="b" * 64,
        deadline_epoch_ms=time.time_ns() // 1_000_000 + 30_000,
    )


def test_hub_task_queue_port_tracks_success_without_persisting_input(app) -> None:
    from agent.db_models import TaskDB
    from agent.repository import task_repo

    request = _request(secret="TOP-SECRET-VOICE-TRANSCRIPT")
    port = HubTaskQueueRestrictedInferencePort(_SuccessfulPort())

    with app.app_context():
        task_repo.save(
            TaskDB(
                id=request.task_id,
                task_kind="voice_transcription",
                worker_execution_context={
                    "voice_transcription": {
                        "tenant_scope_hash": hashlib.sha256(request.tenant_id.encode()).hexdigest(),
                        "owner_subject_hash": "c" * 64,
                        "profile_id": "voice-profile-a",
                    }
                },
            )
        )
        response = port.execute(request)
        tracking_id = port._tracking_task_id(request)
        tracked = task_repo.get_by_id(tracking_id)

    assert response.no_generation is True
    assert tracked is not None
    assert tracked.status == "completed"
    assert tracked.parent_task_id == request.task_id
    assert tracked.task_kind == "restricted_inference"
    assert tracked.worker_execution_context["restricted_inference"]["no_generation"] is True
    assert tracked.worker_execution_context["restricted_inference"]["owner_subject_hash"] == "c" * 64
    assert tracked.worker_execution_context["restricted_inference"]["profile_id"] == "voice-profile-a"
    serialized = json.dumps(tracked.model_dump(), default=str)
    assert "TOP-SECRET-VOICE-TRANSCRIPT" not in serialized
    assert request.tenant_id not in serialized
    assert any(event["event_type"] == "restricted_inference_completed" for event in tracked.history)


def test_hub_task_queue_port_tracks_redacted_failure_and_reraises(app) -> None:
    from agent.repository import task_repo

    request = _request(secret="TOP-SECRET-FAILURE-INPUT")
    port = HubTaskQueueRestrictedInferencePort(_FailingPort())

    with app.app_context(), pytest.raises(TimeoutError, match="sensitive worker failure details"):
        port.execute(request)

    with app.app_context():
        tracked = task_repo.get_by_id(port._tracking_task_id(request))

    assert tracked is not None
    assert tracked.status == "failed"
    assert tracked.status_reason_code == "restricted_inference_failed"
    assert tracked.status_reason_details == {"error_type": "TimeoutError"}
    serialized = json.dumps(tracked.model_dump(), default=str)
    assert "TOP-SECRET-FAILURE-INPUT" not in serialized
    assert "sensitive worker failure details" not in serialized
