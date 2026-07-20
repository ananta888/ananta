"""Fail-closed worker clients for Hub authority and artifact publication."""

from __future__ import annotations

import base64
import http.client
import ipaddress
import json
import socket
import urllib.parse
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Sequence

from ananta_contracts.speech_adaptation import SpeechAdaptationJob
from worker.speech_training.backend import (
    SpeechDatasetView,
    SpeechTrainingBackendError,
)
from worker.speech_training.result_publisher import PublicationReceipt

_BASE_PATH = "/internal/v1/speech-adaptation-control"
_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class HttpHubSpeechTrainingPorts:
    """One exact-allowlist HTTP client; it owns no Hub workflow decisions."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        resolver=None,
        connect_timeout_seconds: float = 5.0,
        response_timeout_seconds: float = 30.0,
    ) -> None:
        normalized = _normalize_endpoint(endpoint)
        allowlist = {_normalize_endpoint(value) for value in allowed_endpoints}
        if normalized not in allowlist:
            raise ValueError("speech Hub callback endpoint is not exactly allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 32 or any(character.isspace() for character in token):
            raise ValueError("speech Hub callback token must contain at least 32 characters")
        if not 0 < connect_timeout_seconds <= 60 or not 0 < response_timeout_seconds <= 300:
            raise ValueError("speech Hub callback timeout is invalid")
        parsed = urllib.parse.urlsplit(normalized)
        assert parsed.hostname is not None and parsed.port is not None
        self._hostname = parsed.hostname
        self._port = parsed.port
        self._host_header = f"{parsed.hostname}:{parsed.port}"
        self._token = token
        self._resolver = resolver
        self._connect_timeout = connect_timeout_seconds
        self._response_timeout = response_timeout_seconds

    def verify(self, job: SpeechAdaptationJob, *, phase: str) -> tuple[bool, str | None]:
        payload = {
            "job_id": job.job_id,
            "attempt_id": job.attempt.attempt_id,
            "binding_digest": job.binding_digest,
            "fencing_digest": job.fencing.fencing_digest,
            "phase": phase,
        }
        try:
            response = self._request_json("/authority", payload, expected=(200,))
        except SpeechTrainingBackendError as exc:
            return False, exc.reason_code
        if set(response) != {"active", "reason_code"} or not isinstance(response.get("active"), bool):
            return False, "speech_hub_authority_response_invalid"
        reason = response.get("reason_code")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 128):
            return False, "speech_hub_authority_response_invalid"
        return bool(response["active"]), reason

    def publish(
        self,
        *,
        job_id: str,
        attempt_id: str,
        fencing_digest: str,
        binding_digest: str,
        target_id: str,
        target_ref: str,
        sha256: str,
        size_bytes: int,
        media_type: str,
        stream: BinaryIO,
    ) -> PublicationReceipt:
        metadata = {
            "job_id": job_id,
            "attempt_id": attempt_id,
            "fencing_digest": fencing_digest,
            "binding_digest": binding_digest,
            "target_id": target_id,
            "target_ref": target_ref,
            "sha256": sha256,
            "size_bytes": size_bytes,
            "media_type": media_type,
        }
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(
                    metadata,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        response = self._request_stream(
            "/artifacts",
            stream,
            size_bytes=size_bytes,
            metadata=encoded,
            expected=(201,),
        )
        expected_fields = {"artifact_id", "artifact_ref", "sha256", "size_bytes"}
        if set(response) != expected_fields:
            raise SpeechTrainingBackendError(
                "speech_hub_artifact_response_invalid",
                "Hub artifact response has an invalid shape",
            )
        return PublicationReceipt(
            artifact_id=str(response["artifact_id"]),
            artifact_ref=str(response["artifact_ref"]),
            sha256=str(response["sha256"]),
            size_bytes=int(response["size_bytes"]),
        )

    def _request_json(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        expected: tuple[int, ...],
    ) -> dict[str, Any]:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return self._request(
            path,
            body,
            headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
            expected=expected,
        )

    def _request_stream(
        self,
        path: str,
        stream: BinaryIO,
        *,
        size_bytes: int,
        metadata: str,
        expected: tuple[int, ...],
    ) -> dict[str, Any]:
        return self._request(
            path,
            stream,
            headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(size_bytes),
                "X-Ananta-Artifact-Metadata": metadata,
            },
            expected=expected,
        )

    def _request(
        self,
        path: str,
        body,
        *,
        headers: Mapping[str, str],
        expected: tuple[int, ...],
    ) -> dict[str, Any]:
        try:
            address = _pin_private_address(
                self._hostname,
                self._port,
                resolver=self._resolver,
            )
        except (OSError, ValueError) as exc:
            raise SpeechTrainingBackendError(
                "speech_hub_callback_address_forbidden",
                "Hub callback address was not private and pinned",
                retryable=True,
            ) from exc
        connection = http.client.HTTPConnection(address, self._port, timeout=self._connect_timeout)
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._token}",
            "Host": self._host_header,
            **dict(headers),
        }
        try:
            connection.request("POST", f"{_BASE_PATH}{path}", body=body, headers=request_headers)
            if connection.sock is not None:
                connection.sock.settimeout(self._response_timeout)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise SpeechTrainingBackendError(
                    "speech_hub_callback_redirect_forbidden",
                    "Hub callback redirected unexpectedly",
                )
            raw = response.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                raise SpeechTrainingBackendError(
                    "speech_hub_callback_response_too_large",
                    "Hub callback response exceeded its limit",
                )
            try:
                parsed = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
            except (UnicodeError, ValueError) as exc:
                raise SpeechTrainingBackendError(
                    "speech_hub_callback_response_invalid",
                    "Hub callback response was not finite JSON",
                ) from exc
            if response.status not in expected:
                reason = "speech_hub_callback_rejected"
                if isinstance(parsed, Mapping) and isinstance(parsed.get("error"), Mapping):
                    reason = str(parsed["error"].get("code") or reason)[:128]
                raise SpeechTrainingBackendError(
                    reason,
                    "Hub callback rejected the request",
                    retryable=response.status >= 500,
                )
            if not isinstance(parsed, dict):
                raise SpeechTrainingBackendError(
                    "speech_hub_callback_response_invalid",
                    "Hub callback response must be an object",
                )
            return parsed
        except (OSError, http.client.HTTPException) as exc:
            raise SpeechTrainingBackendError(
                "speech_hub_callback_unavailable",
                "Hub callback was unavailable",
                retryable=True,
            ) from exc
        finally:
            connection.close()


class HubValidatedMockDatasetResolver:
    """Create an empty view only after Hub authority; mock never reads audio."""

    def __init__(self, root: Path, authority: HttpHubSpeechTrainingPorts) -> None:
        self._root = root.resolve()
        self._authority = authority

    def open_admitted(self, job: SpeechAdaptationJob) -> SpeechDatasetView:
        active, reason = self._authority.verify(job, phase="before_audio_access")
        if not active:
            raise SpeechTrainingBackendError(
                str(reason or "speech_dataset_authority_denied"),
                "Hub rejected the current dataset binding",
            )
        root = self._root / job.job_id / job.dataset.dataset_digest
        root.mkdir(parents=True, exist_ok=True)
        return SpeechDatasetView(
            root=root,
            dataset_digest=job.dataset.dataset_digest,
            split_digest=job.dataset.split_digest,
            train_sample_count=job.dataset.train_sample_count,
            validation_sample_count=job.dataset.validation_sample_count,
        )


def _normalize_endpoint(value: str) -> str:
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
        raise ValueError("speech Hub callback endpoint is invalid")
    host = f"[{parsed.hostname.casefold()}]" if ":" in parsed.hostname else parsed.hostname.casefold()
    return urllib.parse.urlunsplit(("http", f"{host}:{parsed.port}", _BASE_PATH, "", ""))


def _pin_private_address(hostname: str, port: int, *, resolver=None) -> str:
    raw: Sequence[str]
    if resolver is not None:
        raw = resolver(hostname, port)
    else:
        try:
            raw = (str(ipaddress.ip_address(hostname)),)
        except ValueError:
            raw = tuple(
                dict.fromkeys(str(item[4][0]) for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM))
            )
    addresses = tuple(dict.fromkeys(str(value) for value in raw))
    if not addresses:
        raise ValueError("speech Hub callback resolved no address")
    parsed = tuple(ipaddress.ip_address(value) for value in addresses)
    if any(
        isinstance(address, ipaddress.IPv6Address)
        and address.ipv4_mapped is not None
        or not any(address.version == network.version and address in network for network in _PRIVATE_NETWORKS)
        for address in parsed
    ):
        raise ValueError("speech Hub callback resolved outside private networks")
    return str(min(parsed, key=lambda value: (value.version, int(value))))


def _reject_constant(_value: str) -> None:
    raise ValueError("non-finite JSON is forbidden")


__all__ = ["HttpHubSpeechTrainingPorts", "HubValidatedMockDatasetResolver"]
