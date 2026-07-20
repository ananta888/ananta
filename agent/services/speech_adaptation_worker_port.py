"""Authenticated Hub transport for the isolated speech-training worker."""

from __future__ import annotations

import http.client
import json
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)
from ananta_contracts.speech_adaptation import (
    CONTRACT_VERSION,
    SpeechAdaptationContractError,
    SpeechAdaptationJob,
    SpeechAdaptationResult,
)

_BASE_PATH = "/internal/v1/speech-training"


class SpeechAdaptationWorkerTransportError(RuntimeError):
    def __init__(self, reason_code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.retryable = retryable


@dataclass(frozen=True)
class SpeechWorkerSubmission:
    job_id: str
    attempt_id: str
    status: str


class HttpSpeechAdaptationWorkerPort:
    """Exact-allowlist, DNS-pinned HTTP adapter owned by the Hub."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        resolver: AddressResolver | None = None,
        connect_timeout_seconds: float = 5.0,
        response_timeout_seconds: float = 30.0,
        max_response_bytes: int = 1024 * 1024,
    ) -> None:
        normalized = normalize_speech_worker_endpoint(endpoint)
        allowlist = {normalize_speech_worker_endpoint(item) for item in allowed_endpoints}
        if normalized not in allowlist:
            raise ValueError("speech training worker endpoint is not exactly allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 24 or any(character.isspace() for character in token):
            raise ValueError("speech training worker token must contain at least 24 non-whitespace characters")
        if not 0 < connect_timeout_seconds <= 60 or not 0 < response_timeout_seconds <= 300:
            raise ValueError("speech training worker timeout is outside its bounds")
        if not 1024 <= max_response_bytes <= 16 * 1024**2:
            raise ValueError("speech training worker response limit is outside its bounds")
        parsed = urllib.parse.urlsplit(normalized)
        assert parsed.hostname is not None and parsed.port is not None
        self._endpoint = normalized
        self._hostname = parsed.hostname
        self._port = parsed.port
        self._host_header = f"{parsed.hostname}:{parsed.port}"
        self._token = token
        self._resolver = resolver
        self._connect_timeout = connect_timeout_seconds
        self._response_timeout = response_timeout_seconds
        self._max_response_bytes = max_response_bytes

    def submit(self, job: SpeechAdaptationJob) -> SpeechWorkerSubmission:
        payload = self._request("POST", f"{_BASE_PATH}/jobs", job.to_dict(), expected=(202,))
        allowed = {"contract_version", "job_id", "attempt_id", "status"}
        if set(payload) != allowed or payload.get("contract_version") != CONTRACT_VERSION:
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_response_invalid",
                "speech worker submission response has an invalid shape",
                retryable=False,
            )
        if payload.get("job_id") != job.job_id or payload.get("attempt_id") != job.attempt.attempt_id:
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_binding_mismatch",
                "speech worker acknowledged a different job attempt",
                retryable=False,
            )
        status = str(payload.get("status") or "")
        if status not in {
            "accepted",
            "running",
            "cancel_requested",
            "completed",
            "dataset_only",
            "cancelled",
            "failed",
        }:
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_status_invalid",
                "speech worker returned an invalid submission status",
                retryable=False,
            )
        return SpeechWorkerSubmission(job_id=job.job_id, attempt_id=job.attempt.attempt_id, status=status)

    def result(self, job: SpeechAdaptationJob) -> SpeechAdaptationResult | None:
        payload = self._request("GET", f"{_BASE_PATH}/jobs/{job.job_id}", None, expected=(200,))
        status = str(payload.get("status") or "")
        if status in {"accepted", "running", "cancel_requested"}:
            return None
        raw_result = payload.get("result")
        if not isinstance(raw_result, Mapping):
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_result_missing",
                "terminal speech worker response has no result",
                retryable=False,
            )
        try:
            result = SpeechAdaptationResult.from_mapping(raw_result)
        except SpeechAdaptationContractError as exc:
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_result_invalid",
                "speech worker returned an invalid terminal result",
                retryable=False,
            ) from exc
        if (
            result.job_id != job.job_id
            or result.attempt_id != job.attempt.attempt_id
            or result.binding_digest != job.binding_digest
            or result.fencing_digest != job.fencing.fencing_digest
        ):
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_result_binding_mismatch",
                "speech worker result is stale or belongs to another binding",
                retryable=False,
            )
        return result

    def cancel(self, job: SpeechAdaptationJob, *, reason_code: str) -> None:
        reason = str(reason_code or "").strip()
        if not reason or len(reason) > 128:
            raise ValueError("speech worker cancellation requires a bounded reason code")
        payload = {
            "attempt_id": job.attempt.attempt_id,
            "fencing_digest": job.fencing.fencing_digest,
            "reason_code": reason,
        }
        response = self._request(
            "POST",
            f"{_BASE_PATH}/jobs/{job.job_id}/cancel",
            payload,
            expected=(200, 202),
        )
        if response.get("job_id") != job.job_id or response.get("status") not in {
            "cancel_requested",
            "cancelled",
            # The worker may win the terminal-state race before processing the
            # cancellation. The Hub still polls and validates that result.
            "completed",
            "failed",
            "dataset_only",
        }:
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_cancel_response_invalid",
                "speech worker cancellation response is invalid",
                retryable=False,
            )

    def drain(self) -> None:
        response = self._request("POST", f"{_BASE_PATH}/drain", {"drain": True}, expected=(200, 202))
        if response != {"status": "draining"}:
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_drain_response_invalid",
                "speech worker drain response is invalid",
                retryable=False,
            )

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None,
        *,
        expected: tuple[int, ...],
    ) -> dict[str, Any]:
        # Resolve for every request. A mixed public/private result is rejected,
        # and the actual socket connects to the pinned private address.
        try:
            address = pin_private_container_address(self._hostname, self._port, resolver=self._resolver)
        except PrivateContainerResolutionError as exc:
            raise SpeechAdaptationWorkerTransportError(
                exc.reason_code,
                "speech worker resolved outside its private allowlisted network",
                retryable=exc.reason_code == "worker_unavailable",
            ) from exc
        body = None if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "Host": self._host_header,
            "X-Ananta-Contract-Version": CONTRACT_VERSION,
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection(address, self._port, timeout=self._connect_timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            if connection.sock is not None:
                connection.sock.settimeout(self._response_timeout)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise SpeechAdaptationWorkerTransportError(
                    "speech_worker_redirect_forbidden",
                    "speech worker transport refused a redirect",
                    retryable=False,
                )
            raw = response.read(self._max_response_bytes + 1)
            if len(raw) > self._max_response_bytes:
                raise SpeechAdaptationWorkerTransportError(
                    "speech_worker_response_too_large",
                    "speech worker response exceeded its byte limit",
                    retryable=False,
                )
            try:
                parsed = json.loads(raw.decode("utf-8"), parse_constant=_reject_non_finite)
            except (UnicodeDecodeError, ValueError) as exc:
                raise SpeechAdaptationWorkerTransportError(
                    "speech_worker_response_invalid",
                    "speech worker response is not finite JSON",
                    retryable=False,
                ) from exc
            if response.status not in expected:
                reason = "speech_worker_request_failed"
                if isinstance(parsed, Mapping):
                    error = parsed.get("error")
                    if isinstance(error, Mapping):
                        reason = str(error.get("code") or reason)[:128]
                retryable = (
                    response.status >= 500
                    or response.status == 429
                    or (response.status == 404 and reason == "speech_job_not_found")
                )
                raise SpeechAdaptationWorkerTransportError(
                    reason,
                    "speech worker rejected the request",
                    retryable=retryable,
                )
            if not isinstance(parsed, dict):
                raise SpeechAdaptationWorkerTransportError(
                    "speech_worker_response_invalid",
                    "speech worker response must be an object",
                    retryable=False,
                )
            return parsed
        except (OSError, http.client.HTTPException) as exc:
            raise SpeechAdaptationWorkerTransportError(
                "speech_worker_unavailable",
                "speech training worker is unavailable",
            ) from exc
        finally:
            connection.close()


def normalize_speech_worker_endpoint(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != _BASE_PATH
    ):
        raise ValueError("speech worker endpoint must be the explicit internal HTTP endpoint")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urllib.parse.urlunsplit(("http", f"{host}:{parsed.port}", _BASE_PATH, "", ""))


def _reject_non_finite(value: str) -> None:
    raise SpeechAdaptationContractError("speech_worker_non_finite", f"non-finite value {value!r} is forbidden")
