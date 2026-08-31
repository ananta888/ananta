"""Exact-allowlist Hub transport for the isolated dendritic-memory worker."""

from __future__ import annotations

import http.client
import ipaddress
import json
import urllib.parse
from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)
from ananta_contracts.dendritic_memory_worker import (
    DENDRITIC_WORKER_BASE_PATH,
    DendriticWorkerAssignmentV1,
    DendriticWorkerResultV1,
)


class DendriticMemoryWorkerTransportError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.retryable = retryable


class HttpDendriticMemoryWorkerPort:
    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        resolver: AddressResolver | None = None,
        timeout_seconds: float = 30,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        normalized = normalize_dendritic_worker_endpoint(endpoint)
        if normalized not in {normalize_dendritic_worker_endpoint(item) for item in allowed_endpoints}:
            raise ValueError("dendritic_worker_endpoint_not_allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 24 or any(character.isspace() for character in token):
            raise ValueError("dendritic_worker_token_invalid")
        if not 0 < timeout_seconds <= 300 or not 1024 <= max_response_bytes <= 16 * 1024**2:
            raise ValueError("dendritic_worker_transport_limits_invalid")
        parsed = urllib.parse.urlsplit(normalized)
        assert parsed.hostname is not None and parsed.port is not None
        self._hostname = parsed.hostname
        self._port = parsed.port
        self._host = f"{parsed.hostname}:{parsed.port}"
        self._token = token
        self._resolver = resolver
        self._timeout = timeout_seconds
        self._max_response = max_response_bytes

    def execute(
        self,
        assignment: Mapping[str, Any],
        *,
        records: Sequence[Mapping[str, Any]] = (),
        packs: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
        parsed_assignment = DendriticWorkerAssignmentV1.from_mapping(assignment)
        body = json.dumps(
            {"assignment": parsed_assignment.to_dict(), "records": list(records), "packs": list(packs)},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        try:
            address = pin_private_container_address(self._hostname, self._port, resolver=self._resolver)
        except PrivateContainerResolutionError as exc:
            raise DendriticMemoryWorkerTransportError(
                exc.reason_code, retryable=exc.reason_code == "worker_unavailable"
            ) from exc
        connection = http.client.HTTPConnection(address, self._port, timeout=self._timeout)
        try:
            connection.request(
                "POST",
                f"{DENDRITIC_WORKER_BASE_PATH}/jobs",
                body=body,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Host": self._host,
                },
            )
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise DendriticMemoryWorkerTransportError(
                    "dendritic_worker_redirect_forbidden", retryable=False
                )
            raw = response.read(self._max_response + 1)
            if len(raw) > self._max_response:
                raise DendriticMemoryWorkerTransportError(
                    "dendritic_worker_response_too_large", retryable=False
                )
            try:
                payload = json.loads(raw.decode(), parse_constant=_reject_non_finite)
            except (UnicodeDecodeError, ValueError) as exc:
                raise DendriticMemoryWorkerTransportError(
                    "dendritic_worker_response_invalid", retryable=False
                ) from exc
            if response.status != 200 or not isinstance(payload, Mapping):
                raise DendriticMemoryWorkerTransportError(
                    "dendritic_worker_request_failed", retryable=response.status >= 500
                )
            result = DendriticWorkerResultV1.from_mapping(payload)
            if (
                result.run_id != parsed_assignment.run_id
                or result.attempt_id != parsed_assignment.attempt_id
                or result.fencing_token != parsed_assignment.fencing_token
            ):
                raise DendriticMemoryWorkerTransportError(
                    "dendritic_worker_result_binding_invalid", retryable=False
                )
            return result.to_dict()
        except (OSError, http.client.HTTPException) as exc:
            raise DendriticMemoryWorkerTransportError(
                "dendritic_worker_unavailable", retryable=True
            ) from exc
        finally:
            connection.close()


def normalize_dendritic_worker_endpoint(value: str) -> str:
    parsed = urllib.parse.urlsplit(str(value or "").strip())
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != DENDRITIC_WORKER_BASE_PATH
    ):
        raise ValueError("dendritic_worker_endpoint_invalid")
    hostname = parsed.hostname.casefold()
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("dendritic_worker_endpoint_invalid")
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urllib.parse.urlunsplit(
        ("http", f"{host}:{parsed.port}", DENDRITIC_WORKER_BASE_PATH, "", "")
    )


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"non-finite value {value!r} is forbidden")


__all__ = [
    "DendriticMemoryWorkerTransportError",
    "HttpDendriticMemoryWorkerPort",
    "normalize_dendritic_worker_endpoint",
]
