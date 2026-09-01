"""Authenticated Hub port for the isolated Spreadsheet Studio worker."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from agent.services.private_container_network_policy import (
    AddressResolver,
    PrivateContainerResolutionError,
    pin_private_container_address,
)
from ananta_contracts.file_credentials import (
    FileCredentialConfigurationError,
    read_file_managed_token,
)
from ananta_contracts.spreadsheet_studio import canonical_digest

_BASE_PATH = "/internal/v1/spreadsheet"
_CONTRACT = "ananta.spreadsheet-worker.v1"


class SpreadsheetWorkerTransportError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = True) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise SpreadsheetWorkerTransportError("spreadsheet_worker_redirect_forbidden", retryable=False)


class HttpSpreadsheetExecutionAdapter:
    """DIP adapter that binds every result to one exact dry-run request."""

    def __init__(
        self,
        *,
        endpoint: str,
        allowed_endpoints: tuple[str, ...],
        bearer_token: str,
        timeout_seconds: float = 120.0,
        max_response_bytes: int = 64 * 1024 * 1024,
        resolver: AddressResolver | None = None,
        opener: Any | None = None,
    ) -> None:
        normalized = normalize_spreadsheet_worker_endpoint(endpoint)
        allowed = {normalize_spreadsheet_worker_endpoint(value) for value in allowed_endpoints}
        if normalized not in allowed:
            raise ValueError("spreadsheet_worker_endpoint_not_allowlisted")
        token = str(bearer_token or "").strip()
        if len(token) < 24 or any(character.isspace() for character in token):
            raise ValueError("spreadsheet_worker_token_invalid")
        if not 1 <= float(timeout_seconds) <= 300:
            raise ValueError("spreadsheet_worker_timeout_invalid")
        if not 1_024 <= int(max_response_bytes) <= 128 * 1024 * 1024:
            raise ValueError("spreadsheet_worker_response_limit_invalid")
        self._parsed = urllib.parse.urlsplit(normalized)
        self._token = token
        self._timeout = float(timeout_seconds)
        self._max_response_bytes = int(max_response_bytes)
        self._resolver = resolver
        self._opener = opener or urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirectHandler(),
        )

    @property
    def capability(self) -> Mapping[str, Any]:
        payload = self._request("GET", "/capabilities")
        if payload.get("contract") != _CONTRACT:
            raise SpreadsheetWorkerTransportError("spreadsheet_worker_contract_mismatch", retryable=False)
        return payload

    def dry_run(
        self,
        *,
        snapshot: Mapping[str, Any],
        actions: tuple[Mapping[str, Any], ...],
    ) -> Mapping[str, Any]:
        body = {
            "contract": _CONTRACT,
            "operation": "dry_run",
            "snapshot": dict(snapshot),
            "actions": [dict(action) for action in actions],
        }
        request_digest = canonical_digest(body)
        payload = self._request("POST", "/dry-runs", body={**body, "request_digest": request_digest})
        if (
            payload.get("contract") != _CONTRACT
            or payload.get("request_digest") != request_digest
            or payload.get("status") != "succeeded"
            or not isinstance(payload.get("result"), Mapping)
        ):
            raise SpreadsheetWorkerTransportError(
                "spreadsheet_worker_response_binding_mismatch",
                retryable=False,
            )
        return dict(payload["result"])

    def import_document(
        self,
        *,
        content: bytes,
        filename: str,
        media_type: str,
        document_version_id: str,
    ) -> Mapping[str, Any]:
        body = {
            "contract": _CONTRACT,
            "operation": "import_document",
            "filename": str(filename),
            "media_type": str(media_type),
            "document_version_id": str(document_version_id),
            "content_base64": base64.b64encode(content).decode("ascii"),
        }
        request_digest = canonical_digest(body)
        payload = self._request("POST", "/imports", body={**body, "request_digest": request_digest})
        if (
            payload.get("contract") != _CONTRACT
            or payload.get("request_digest") != request_digest
            or payload.get("status") != "succeeded"
            or not isinstance(payload.get("result"), Mapping)
        ):
            raise SpreadsheetWorkerTransportError(
                "spreadsheet_worker_response_binding_mismatch",
                retryable=False,
            )
        return dict(payload["result"])

    def _request(
        self,
        method: str,
        suffix: str,
        *,
        body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            address = pin_private_container_address(
                str(self._parsed.hostname or ""),
                int(self._parsed.port or 0),
                resolver=self._resolver,
            )
        except PrivateContainerResolutionError as exc:
            raise SpreadsheetWorkerTransportError(exc.reason_code) from exc
        netloc = f"[{address}]:{self._parsed.port}" if ":" in address else f"{address}:{self._parsed.port}"
        url = urllib.parse.urlunsplit(("http", netloc, f"{_BASE_PATH}{suffix}", "", ""))
        encoded = (
            json.dumps(dict(body), separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
            if body is not None
            else None
        )
        request = urllib.request.Request(
            url,
            data=encoded,
            method=method,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Host": self._parsed.netloc,
            },
        )
        try:
            response = self._opener.open(request, timeout=self._timeout)
            payload = self._read_json(response)
        except urllib.error.HTTPError as exc:
            payload = self._read_json(exc)
            error = payload.get("error") if isinstance(payload, dict) else None
            reason = (
                str(error.get("code") or "spreadsheet_worker_rejected")
                if isinstance(error, dict)
                else "spreadsheet_worker_rejected"
            )
            retryable = bool(error.get("retryable")) if isinstance(error, dict) else exc.code >= 500
            raise SpreadsheetWorkerTransportError(reason, retryable=retryable) from exc
        except SpreadsheetWorkerTransportError:
            raise
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            raise SpreadsheetWorkerTransportError("spreadsheet_worker_unavailable") from exc
        if not isinstance(payload, dict):
            raise SpreadsheetWorkerTransportError("spreadsheet_worker_response_invalid", retryable=False)
        return payload

    def _read_json(self, response: Any) -> Any:
        if "application/json" not in str(response.headers.get("Content-Type") or "").lower():
            raise SpreadsheetWorkerTransportError("spreadsheet_worker_response_invalid", retryable=False)
        raw = response.read(self._max_response_bytes + 1)
        if len(raw) > self._max_response_bytes:
            raise SpreadsheetWorkerTransportError("spreadsheet_worker_response_too_large", retryable=False)
        try:
            return json.loads(raw.decode())
        except (UnicodeError, ValueError) as exc:
            raise SpreadsheetWorkerTransportError("spreadsheet_worker_response_invalid", retryable=False) from exc


def normalize_spreadsheet_worker_endpoint(value: str) -> str:
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
        raise ValueError("spreadsheet_worker_endpoint_invalid")
    hostname = parsed.hostname.casefold()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return urllib.parse.urlunsplit(("http", f"{host}:{parsed.port}", _BASE_PATH, "", ""))


def spreadsheet_worker_port_from_environment() -> HttpSpreadsheetExecutionAdapter:
    endpoint = str(os.getenv("ANANTA_SPREADSHEET_WORKER_URL") or "").strip()
    allowed = tuple(
        item.strip()
        for item in str(os.getenv("ANANTA_SPREADSHEET_ALLOWED_ENDPOINTS") or "").split(",")
        if item.strip()
    )
    if not endpoint or not allowed:
        raise RuntimeError("spreadsheet_worker_endpoint_unconfigured")
    token = _worker_token()
    return HttpSpreadsheetExecutionAdapter(
        endpoint=endpoint,
        allowed_endpoints=allowed,
        bearer_token=token,
        timeout_seconds=float(os.getenv("ANANTA_SPREADSHEET_WORKER_TIMEOUT_SECONDS", "120")),
    )


def _worker_token() -> str:
    inline = str(os.getenv("ANANTA_SPREADSHEET_WORKER_TOKEN") or "").strip()
    path = str(os.getenv("ANANTA_SPREADSHEET_WORKER_TOKEN_FILE") or "").strip()
    if path:
        try:
            managed = read_file_managed_token(
                path,
                description="Spreadsheet worker token file",
                min_bytes=24,
                max_bytes=16_384,
            )
        except FileCredentialConfigurationError as exc:
            raise RuntimeError(str(exc)) from exc
        if inline and inline != managed:
            raise RuntimeError("spreadsheet_worker_token_sources_conflict")
        return managed
    if len(inline) < 24:
        raise RuntimeError("spreadsheet_worker_token_unconfigured")
    return inline


__all__ = [
    "HttpSpreadsheetExecutionAdapter",
    "SpreadsheetWorkerTransportError",
    "normalize_spreadsheet_worker_endpoint",
    "spreadsheet_worker_port_from_environment",
]
