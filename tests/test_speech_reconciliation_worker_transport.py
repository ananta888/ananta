from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import pytest

from agent.services.speech_reconciliation_scheduler import (
    ScheduledSpeechReconciliation,
    SpeechReconciliationLease,
)
from agent.services.speech_reconciliation_task_port import SpeechReconciliationTaskReference
from agent.services.speech_reconciliation_worker_port import (
    HttpSpeechReconciliationWorkerPort,
    HubSpeechReconciliationAttemptDispatcher,
    SpeechReconciliationAudioUpload,
    SpeechReconciliationWorkerSubmission,
    SpeechReconciliationWorkerTransportError,
)
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationBudgetLedger,
    SpeechResourceVector,
)
from ananta_contracts.speech_reconciliation_worker import (
    SpeechReconciliationExecutionPlan,
    SpeechReconciliationWorkerTask,
)
from tests.speech_reconciliation_support import job_contract, worker_outcome_payload

ENDPOINT = "http://speech-reconciliation-worker:8098/internal/v1/speech-reconciliation"
TOKEN = "speech-reconciliation-transport-token"


def _task(ciphertext: bytes = b"x" * 64) -> SpeechReconciliationWorkerTask:
    job = job_contract()
    zero = SpeechResourceVector()
    allocated = SpeechResourceVector(
        wall_time_ms=60_000,
        cpu_time_ms=60_000,
        memory_byte_ms=1_000_000,
        disk_bytes=1_000_000,
        checkpoint_bytes=100_000,
    )
    return SpeechReconciliationWorkerTask.from_mapping(
        {
            "contract_version": CONTRACT_VERSION,
            "task_type": "speech_reconciliation_attempt",
            "job": job.to_dict(),
            "budget_ledger": {
                "contract_version": CONTRACT_VERSION,
                "job_id": job.job_id,
                "attempt_id": job.attempt_id,
                "fencing_epoch": job.fencing_epoch,
                "sequence": job.ledger_sequence,
                "stage": job.stage,
                "source_duration_ms": job.source_duration_ms,
                "compute_factor": job.max_compute_factor,
                "allocated": allocated.to_dict(),
                "reserved": zero.to_dict(),
                "consumed": zero.to_dict(),
                "remaining": allocated.to_dict(),
            },
            "audio_artifact": {
                "artifact_ref": job.input_artifact_ref,
                "transport_digest": hashlib.sha256(ciphertext).hexdigest(),
                "content_digest": hashlib.sha256(b"plaintext").hexdigest(),
                "filename": "input.wav",
                "content_type": "audio/wav",
                "ciphertext_bytes": len(ciphertext),
                "plaintext_bytes": 32,
                "decoded_pcm_bytes": 128,
                "duration_ms": 1000,
                "key_epoch": job.key_epoch,
            },
            "execution_plan": {
                "max_parallel_passes": 1,
                "pass_deadline_ms": 10_000,
                "passes": [
                    {
                        "pass_id": "pass-a",
                        "model_id": "model-a",
                        "model_revision": "revision-a",
                        "variant_id": "original",
                        "language": "de",
                    }
                ],
            },
        }
    )


@dataclass
class _Response:
    status: int
    payload: object
    content_type: str = "application/json"

    def read(self, _maximum: int) -> bytes:
        return json.dumps(self.payload).encode()

    def getheader(self, name: str):
        return self.content_type if name.casefold() == "content-type" else None


class _Connection:
    responses: list[_Response] = []
    calls: list[tuple[str, str, object, dict[str, str], str, int]] = []
    failure: Exception | None = None

    def __init__(self, address: str, port: int, *, timeout: float) -> None:
        self.address = address
        self.port = port
        self.timeout = timeout
        self.sock = None

    def request(self, method: str, path: str, *, body=None, headers=None) -> None:
        if self.failure is not None:
            raise self.failure
        self.calls.append((method, path, body, dict(headers or {}), self.address, self.port))

    def getresponse(self):
        return self.responses.pop(0)

    def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_connection(monkeypatch):
    _Connection.responses = []
    _Connection.calls = []
    _Connection.failure = None
    monkeypatch.setattr("agent.services.speech_reconciliation_worker_port.http.client.HTTPConnection", _Connection)


def _port() -> HttpSpeechReconciliationWorkerPort:
    return HttpSpeechReconciliationWorkerPort(
        endpoint=ENDPOINT,
        allowed_endpoints=(ENDPOINT,),
        bearer_token=TOKEN,
        resolver=lambda _hostname, _port: ("172.18.0.8",),
    )


def _submission(task: SpeechReconciliationWorkerTask, status: str) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "job_id": task.job.job_id,
        "attempt_id": task.job.attempt_id,
        "fencing_epoch": task.job.fencing_epoch,
        "status": status,
    }


def test_authenticated_pinned_submit_upload_poll_and_cancel_roundtrip() -> None:
    ciphertext = b"x" * 64
    task = _task(ciphertext)
    _Connection.responses = [
        _Response(202, _submission(task, "awaiting_audio")),
        _Response(202, _submission(task, "accepted")),
        _Response(
            200,
            {
                "contract_version": CONTRACT_VERSION,
                "job_id": task.job.job_id,
                "attempt_id": task.job.attempt_id,
                "fencing_epoch": task.job.fencing_epoch,
                "status": "completed",
                "result": worker_outcome_payload(task.job),
            },
        ),
        _Response(202, {"job_id": task.job.job_id, "status": "completed"}),
    ]
    port = _port()
    assert port.submit(task).status == "awaiting_audio"
    assert port.upload_audio(task, ciphertext).status == "accepted"
    poll = port.poll(task.job)
    assert poll.result is not None and poll.result.status == "completed"
    assert port.cancel(task.job) == "completed"
    assert all(call[4:] == ("172.18.0.8", 8098) for call in _Connection.calls)
    headers = _Connection.calls[0][3]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Host"] == "speech-reconciliation-worker:8098"
    assert headers["X-Ananta-Contract-Version"] == CONTRACT_VERSION


def test_artifact_digest_is_checked_before_any_upload() -> None:
    task = _task(b"x" * 64)
    with pytest.raises(SpeechReconciliationWorkerTransportError) as error:
        _port().upload_audio(task, b"y" * 64)
    assert error.value.reason_code == "speech_reconciliation_artifact_transport_tamper"
    assert not _Connection.calls


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (413, "speech_reconciliation_request_too_large"),
        (404, "speech_reconciliation_job_not_found"),
        (409, "speech_reconciliation_job_binding_conflict"),
    ],
)
def test_413_404_and_409_are_content_free_non_retryable(status: int, reason: str) -> None:
    _Connection.responses = [
        _Response(status, {"error": {"reason_code": reason, "retryable": False}, "detail": "SECRET AUDIO"})
    ]
    with pytest.raises(SpeechReconciliationWorkerTransportError) as error:
        _port().poll(_task().job)
    assert error.value.reason_code == reason and not error.value.retryable
    assert "SECRET" not in str(error.value)


def test_timeout_invalid_content_type_and_stale_result_fail_closed() -> None:
    _Connection.failure = TimeoutError("SECRET TRANSCRIPT")
    with pytest.raises(SpeechReconciliationWorkerTransportError) as timeout:
        _port().poll(_task().job)
    assert timeout.value.reason_code == "speech_reconciliation_worker_unavailable"
    assert "SECRET" not in str(timeout.value)

    _Connection.failure = None
    _Connection.responses = [_Response(200, {"secret": "payload"}, "text/plain")]
    with pytest.raises(SpeechReconciliationWorkerTransportError) as content_type:
        _port().poll(_task().job)
    assert content_type.value.reason_code == "speech_reconciliation_worker_content_type_invalid"

    task = _task()
    stale = worker_outcome_payload(task.job, fencing_epoch=task.job.fencing_epoch + 1)
    _Connection.responses = [
        _Response(
            200,
            {
                "contract_version": CONTRACT_VERSION,
                "job_id": task.job.job_id,
                "attempt_id": task.job.attempt_id,
                "fencing_epoch": task.job.fencing_epoch,
                "status": "completed",
                "result": stale,
            },
        )
    ]
    with pytest.raises(SpeechReconciliationWorkerTransportError) as binding:
        _port().poll(task.job)
    assert binding.value.reason_code == "speech_reconciliation_worker_result_invalid"


def test_dispatcher_builds_one_shared_task_before_artifact_upload() -> None:
    task = _task()

    class _Worker:
        def __init__(self) -> None:
            self.calls = []

        def submit(self, submitted):
            self.calls.append(("submit", submitted))
            return SpeechReconciliationWorkerSubmission(
                submitted.job.job_id,
                submitted.job.attempt_id,
                submitted.job.fencing_epoch,
                "awaiting_audio",
            )

        def upload_audio(self, submitted, ciphertext):
            self.calls.append(("upload", submitted, ciphertext))
            return SpeechReconciliationWorkerSubmission(
                submitted.job.job_id,
                submitted.job.attempt_id,
                submitted.job.fencing_epoch,
                "accepted",
            )

    class _Lookup:
        def get(self, *, job_id: str) -> SpeechReconciliationBudgetLedger | None:
            assert job_id == task.job.job_id
            return task.budget_ledger

    class _Artifacts:
        def resolve(self, job):
            assert job == task.job
            return SpeechReconciliationAudioUpload(task.audio_artifact, b"x" * 64)

    class _Plans:
        def resolve(self, job) -> SpeechReconciliationExecutionPlan:
            assert job == task.job
            return task.execution_plan

    worker = _Worker()
    scheduled = ScheduledSpeechReconciliation(
        SpeechReconciliationLease("lease-a", task.job, "worker-a", 30_000),
        SpeechReconciliationTaskReference("parent-a", "attempt-a", "assigned"),
    )
    result = HubSpeechReconciliationAttemptDispatcher(
        worker=worker,
        artifacts=_Artifacts(),
        ledgers=_Lookup(),
        plans=_Plans(),
    ).dispatch(scheduled)
    assert result.status == "accepted"
    assert [call[0] for call in worker.calls] == ["submit", "upload"]
    assert worker.calls[0][1].binding_digest == task.binding_digest
