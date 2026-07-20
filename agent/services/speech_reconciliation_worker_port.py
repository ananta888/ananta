"""Authenticated Hub transport and dispatch boundary for reconciliation workers."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import urllib.parse
from dataclasses import dataclass
from typing import Mapping, Protocol

from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)
from agent.services.speech_reconciliation_scheduler import ScheduledSpeechReconciliation
from ananta_contracts.speech_reconciliation import (
    CONTRACT_VERSION,
    SpeechReconciliationBudgetLedger,
    SpeechReconciliationContractError,
    SpeechReconciliationJob,
)
from ananta_contracts.speech_reconciliation_worker import (
    MAX_AUDIO_CIPHERTEXT_BYTES,
    SpeechReconciliationAudioArtifact,
    SpeechReconciliationExecutionPlan,
    SpeechReconciliationWorkerOutcome,
    SpeechReconciliationWorkerTask,
    assert_worker_outcome_matches_job,
)

BASE_PATH = "/internal/v1/speech-reconciliation"


class SpeechReconciliationWorkerTransportError(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 503,
        retryable: bool = True,
    ) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechReconciliationWorkerSubmission:
    job_id: str
    attempt_id: str
    fencing_epoch: int
    status: str


@dataclass(frozen=True)
class SpeechReconciliationWorkerPoll:
    job_id: str
    attempt_id: str
    fencing_epoch: int
    status: str
    result: SpeechReconciliationWorkerOutcome | None


class SpeechReconciliationWorkerPort(Protocol):
    def submit(self, task: SpeechReconciliationWorkerTask) -> SpeechReconciliationWorkerSubmission: ...

    def upload_audio(
        self,
        task: SpeechReconciliationWorkerTask,
        ciphertext: bytes,
    ) -> SpeechReconciliationWorkerSubmission: ...

    def poll(self, job: SpeechReconciliationJob) -> SpeechReconciliationWorkerPoll: ...

    def cancel(self, job: SpeechReconciliationJob) -> str: ...


class HttpSpeechReconciliationWorkerPort:
    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        resolver: AddressResolver | None = None,
        connect_timeout_seconds: float = 5.0,
        response_timeout_seconds: float = 30.0,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        normalized = normalize_speech_reconciliation_endpoint(endpoint)
        allowlist = {normalize_speech_reconciliation_endpoint(item) for item in allowed_endpoints}
        if normalized not in allowlist:
            raise ValueError("speech reconciliation worker endpoint is not exactly allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 24 or any(character.isspace() for character in token):
            raise ValueError("speech reconciliation worker token is invalid")
        if not 0 < connect_timeout_seconds <= 60 or not 0 < response_timeout_seconds <= 300:
            raise ValueError("speech reconciliation worker timeout is invalid")
        if not 1024 <= max_response_bytes <= 16 * 1024**2:
            raise ValueError("speech reconciliation response limit is invalid")
        parsed = urllib.parse.urlsplit(normalized)
        assert parsed.hostname is not None and parsed.port is not None
        self._hostname = parsed.hostname
        self._port = parsed.port
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        self._host_header = f"{host}:{parsed.port}"
        self._token = token
        self._resolver = resolver
        self._connect_timeout = connect_timeout_seconds
        self._response_timeout = response_timeout_seconds
        self._max_response_bytes = max_response_bytes

    def submit(self, task: SpeechReconciliationWorkerTask) -> SpeechReconciliationWorkerSubmission:
        payload = self._request("POST", f"{BASE_PATH}/jobs", json_payload=task.to_dict(), expected=(202,))
        return self._submission(payload, task.job, allowed_statuses={"awaiting_audio", "accepted"})

    def upload_audio(
        self,
        task: SpeechReconciliationWorkerTask,
        ciphertext: bytes,
    ) -> SpeechReconciliationWorkerSubmission:
        payload_bytes = bytes(ciphertext)
        if (
            len(payload_bytes) != task.audio_artifact.ciphertext_bytes
            or len(payload_bytes) > MAX_AUDIO_CIPHERTEXT_BYTES
        ):
            raise SpeechReconciliationWorkerTransportError(
                "speech_reconciliation_artifact_size_mismatch",
                status_code=422,
                retryable=False,
            )
        if not hmac.compare_digest(
            hashlib.sha256(payload_bytes).hexdigest(),
            task.audio_artifact.transport_digest,
        ):
            raise SpeechReconciliationWorkerTransportError(
                "speech_reconciliation_artifact_transport_tamper",
                status_code=409,
                retryable=False,
            )
        payload = self._request(
            "PUT",
            f"{BASE_PATH}/jobs/{task.job.job_id}/audio",
            binary_payload=payload_bytes,
            expected=(202,),
        )
        return self._submission(payload, task.job, allowed_statuses={"accepted", "running"})

    def poll(self, job: SpeechReconciliationJob) -> SpeechReconciliationWorkerPoll:
        payload = self._request("GET", f"{BASE_PATH}/jobs/{job.job_id}", expected=(200,))
        expected = {"contract_version", "job_id", "attempt_id", "fencing_epoch", "status", "result"}
        if set(payload) != expected or payload.get("contract_version") != CONTRACT_VERSION:
            raise self._invalid("speech_reconciliation_worker_response_invalid")
        if (
            payload.get("job_id") != job.job_id
            or payload.get("attempt_id") != job.attempt_id
            or payload.get("fencing_epoch") != job.fencing_epoch
        ):
            raise self._invalid("speech_reconciliation_worker_response_binding_mismatch")
        status = str(payload.get("status") or "")
        allowed = {
            "awaiting_audio",
            "accepted",
            "running",
            "cancel_requested",
            "completed",
            "partial",
            "failed",
            "cancelled",
        }
        if status not in allowed:
            raise self._invalid("speech_reconciliation_worker_status_invalid")
        raw_result = payload.get("result")
        if status in {"completed", "partial", "failed", "cancelled"}:
            if not isinstance(raw_result, Mapping):
                raise self._invalid("speech_reconciliation_worker_result_missing")
            try:
                result: SpeechReconciliationWorkerOutcome | None = (
                    SpeechReconciliationWorkerOutcome.from_mapping(raw_result)
                )
                assert_worker_outcome_matches_job(job, result)
            except SpeechReconciliationContractError as exc:
                raise self._invalid("speech_reconciliation_worker_result_invalid") from exc
            if result.status != status:
                raise self._invalid("speech_reconciliation_worker_result_status_mismatch")
        elif raw_result is not None:
            raise self._invalid("speech_reconciliation_worker_result_early")
        else:
            result = None
        return SpeechReconciliationWorkerPoll(job.job_id, job.attempt_id, job.fencing_epoch, status, result)

    def cancel(self, job: SpeechReconciliationJob) -> str:
        payload = self._request(
            "POST",
            f"{BASE_PATH}/jobs/{job.job_id}/cancel",
            json_payload={
                "attempt_id": job.attempt_id,
                "fencing_token_digest": job.fencing_token_digest,
            },
            expected=(202,),
        )
        if set(payload) != {"job_id", "status"} or payload.get("job_id") != job.job_id:
            raise self._invalid("speech_reconciliation_worker_cancel_response_invalid")
        status = str(payload.get("status") or "")
        if status not in {"cancel_requested", "cancelled", "completed", "partial", "failed"}:
            raise self._invalid("speech_reconciliation_worker_cancel_response_invalid")
        return status

    def _submission(
        self,
        payload: Mapping[str, object],
        job: SpeechReconciliationJob,
        *,
        allowed_statuses: set[str],
    ) -> SpeechReconciliationWorkerSubmission:
        expected = {"contract_version", "job_id", "attempt_id", "fencing_epoch", "status"}
        if set(payload) != expected or payload.get("contract_version") != CONTRACT_VERSION:
            raise self._invalid("speech_reconciliation_worker_response_invalid")
        if (
            payload.get("job_id") != job.job_id
            or payload.get("attempt_id") != job.attempt_id
            or payload.get("fencing_epoch") != job.fencing_epoch
            or payload.get("status") not in allowed_statuses
        ):
            raise self._invalid("speech_reconciliation_worker_response_binding_mismatch")
        return SpeechReconciliationWorkerSubmission(
            job.job_id,
            job.attempt_id,
            job.fencing_epoch,
            str(payload["status"]),
        )

    @staticmethod
    def _invalid(reason_code: str) -> SpeechReconciliationWorkerTransportError:
        return SpeechReconciliationWorkerTransportError(reason_code, status_code=502, retryable=False)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: Mapping[str, object] | None = None,
        binary_payload: bytes | None = None,
        expected: tuple[int, ...],
    ) -> dict[str, object]:
        if json_payload is not None and binary_payload is not None:
            raise ValueError("speech reconciliation request payload is ambiguous")
        try:
            address = pin_private_container_address(self._hostname, self._port, resolver=self._resolver)
        except PrivateContainerResolutionError as exc:
            raise SpeechReconciliationWorkerTransportError(
                exc.reason_code,
                retryable=exc.reason_code == "worker_unavailable",
            ) from exc
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "Host": self._host_header,
            "X-Ananta-Contract-Version": CONTRACT_VERSION,
        }
        body: str | bytes | None = None
        if json_payload is not None:
            body = json.dumps(json_payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
            if len(body.encode()) > 1024 * 1024:
                raise SpeechReconciliationWorkerTransportError(
                    "speech_reconciliation_task_size_limit",
                    status_code=413,
                    retryable=False,
                )
            headers["Content-Type"] = "application/json"
        elif binary_payload is not None:
            body = binary_payload
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(len(binary_payload))
        connection = http.client.HTTPConnection(address, self._port, timeout=self._connect_timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            if connection.sock is not None:
                connection.sock.settimeout(self._response_timeout)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise SpeechReconciliationWorkerTransportError(
                    "speech_reconciliation_worker_redirect_forbidden",
                    status_code=502,
                    retryable=False,
                )
            raw = response.read(self._max_response_bytes + 1)
            if len(raw) > self._max_response_bytes:
                raise self._invalid("speech_reconciliation_worker_response_too_large")
            content_type = str(response.getheader("Content-Type") or "").split(";", 1)[0].strip().casefold()
            if content_type != "application/json":
                raise self._invalid("speech_reconciliation_worker_content_type_invalid")
            try:
                parsed = json.loads(raw.decode(), parse_constant=_reject_non_finite)
            except (UnicodeDecodeError, ValueError) as exc:
                raise self._invalid("speech_reconciliation_worker_response_invalid") from exc
            if response.status not in expected:
                reason = "speech_reconciliation_worker_request_failed"
                retryable = response.status >= 500
                if isinstance(parsed, Mapping) and isinstance(parsed.get("error"), Mapping):
                    error = parsed["error"]
                    candidate = str(error.get("reason_code") or "")
                    if candidate and len(candidate) <= 128 and not any(character.isspace() for character in candidate):
                        reason = candidate
                    retryable = bool(error.get("retryable")) and response.status >= 500
                raise SpeechReconciliationWorkerTransportError(
                    reason,
                    status_code=response.status,
                    retryable=retryable,
                )
            if not isinstance(parsed, dict):
                raise self._invalid("speech_reconciliation_worker_response_invalid")
            return parsed
        except SpeechReconciliationWorkerTransportError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise SpeechReconciliationWorkerTransportError("speech_reconciliation_worker_unavailable") from exc
        finally:
            connection.close()


@dataclass(frozen=True)
class SpeechReconciliationAudioUpload:
    artifact: SpeechReconciliationAudioArtifact
    ciphertext: bytes


class SpeechReconciliationArtifactTransferPort(Protocol):
    def resolve(self, job: SpeechReconciliationJob) -> SpeechReconciliationAudioUpload: ...


class SpeechReconciliationLedgerLookupPort(Protocol):
    def get(self, *, job_id: str) -> SpeechReconciliationBudgetLedger | None: ...


class SpeechReconciliationExecutionPlanPort(Protocol):
    def resolve(self, job: SpeechReconciliationJob) -> SpeechReconciliationExecutionPlan: ...


class HubSpeechReconciliationAttemptDispatcher:
    """Resolve Hub-owned artifacts/policy and submit exactly one leased attempt."""

    def __init__(
        self,
        *,
        worker: SpeechReconciliationWorkerPort,
        artifacts: SpeechReconciliationArtifactTransferPort,
        ledgers: SpeechReconciliationLedgerLookupPort,
        plans: SpeechReconciliationExecutionPlanPort,
    ) -> None:
        self._worker = worker
        self._artifacts = artifacts
        self._ledgers = ledgers
        self._plans = plans

    def dispatch(self, scheduled: ScheduledSpeechReconciliation) -> SpeechReconciliationWorkerSubmission:
        job = scheduled.lease.job
        ledger = self._ledgers.get(job_id=job.job_id)
        if ledger is None:
            raise SpeechReconciliationWorkerTransportError(
                "speech_reconciliation_ledger_not_found",
                status_code=409,
                retryable=False,
            )
        upload = self._artifacts.resolve(job)
        plan = self._plans.resolve(job)
        task = SpeechReconciliationWorkerTask.from_mapping(
            {
                "contract_version": CONTRACT_VERSION,
                "task_type": "speech_reconciliation_attempt",
                "job": job.to_dict(),
                "budget_ledger": ledger.to_dict(),
                "audio_artifact": upload.artifact.to_dict(),
                "execution_plan": plan.to_dict(),
            }
        )
        self._worker.submit(task)
        return self._worker.upload_audio(task, upload.ciphertext)


def normalize_speech_reconciliation_endpoint(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != BASE_PATH
    ):
        raise ValueError("speech reconciliation worker endpoint is invalid")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urllib.parse.urlunsplit(("http", f"{host}:{parsed.port}", BASE_PATH, "", ""))


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite JSON value forbidden: {value}")


__all__ = [
    "BASE_PATH",
    "HttpSpeechReconciliationWorkerPort",
    "HubSpeechReconciliationAttemptDispatcher",
    "SpeechReconciliationArtifactTransferPort",
    "SpeechReconciliationAudioUpload",
    "SpeechReconciliationExecutionPlanPort",
    "SpeechReconciliationLedgerLookupPort",
    "SpeechReconciliationWorkerPoll",
    "SpeechReconciliationWorkerPort",
    "SpeechReconciliationWorkerSubmission",
    "SpeechReconciliationWorkerTransportError",
    "normalize_speech_reconciliation_endpoint",
]
