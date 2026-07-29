"""Minimal, fail-closed MCP Streamable HTTP session composition."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from threading import RLock
from typing import Any, Protocol

from ananta_contracts.unsloth_studio import (
    IncompatibleUnslothStudioContract,
)

_PROTOCOL_VERSION = "2025-06-18"
_MCP_PATH = "/mcp/"


class JsonHttpTransportPort(Protocol):
    def request_json(
        self,
        *,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        service_bearer_secret_ref: str | None = None,
        response_headers: MutableMapping[str, str] | None = None,
    ) -> Mapping[str, Any]: ...


class McpStreamableHttpTransport:
    """Add the mandatory MCP initialize/session exchange to a JSON transport."""

    def __init__(self, transport: JsonHttpTransportPort) -> None:
        self._transport = transport
        self._lock = RLock()
        self._session_id: str | None = None
        self._initialized_for: tuple[str, str] | None = None

    def request_json(self, **values: Any) -> Mapping[str, Any]:
        payload = values.get("payload")
        if not isinstance(payload, Mapping):
            return self._transport.request_json(**values)
        method = str(payload.get("method") or "")
        if method not in {"tools/list", "tools/call"}:
            return self._transport.request_json(**values)
        path = str(values.get("path") or "")
        secret_ref = str(
            values.get("service_bearer_secret_ref") or ""
        )
        if path != _MCP_PATH or not secret_ref:
            raise IncompatibleUnslothStudioContract(
                "incompatible_upstream_contract"
            )
        with self._lock:
            self._ensure_initialized(
                path=path,
                secret_ref=secret_ref,
            )
            request_values = dict(values)
            request_values["headers"] = self._session_headers(
                values.get("headers")
            )
            return self._transport.request_json(**request_values)

    def _ensure_initialized(
        self,
        *,
        path: str,
        secret_ref: str,
    ) -> None:
        binding = (path, secret_ref)
        if self._initialized_for == binding:
            return
        response_headers: dict[str, str] = {}
        request_id = "ananta-mcp-initialize-v1"
        response = self._transport.request_json(
            method="POST",
            path=path,
            payload={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "ananta",
                        "version": "1",
                    },
                },
            },
            headers=self._base_headers(),
            service_bearer_secret_ref=secret_ref,
            response_headers=response_headers,
        )
        result = response.get("result")
        if (
            response.get("jsonrpc") != "2.0"
            or response.get("id") != request_id
            or not isinstance(result, Mapping)
            or result.get("protocolVersion") != _PROTOCOL_VERSION
            or not isinstance(result.get("capabilities"), Mapping)
            or not isinstance(result.get("serverInfo"), Mapping)
        ):
            raise IncompatibleUnslothStudioContract(
                "incompatible_upstream_contract"
            )
        normalized_headers = {
            str(name).lower(): str(value)
            for name, value in response_headers.items()
        }
        session_id = normalized_headers.get("mcp-session-id")
        if session_id is not None and (
            not 1 <= len(session_id) <= 128
            or not session_id.isascii()
            or any(
                not 0x21 <= ord(character) <= 0x7E
                for character in session_id
            )
        ):
            raise IncompatibleUnslothStudioContract(
                "incompatible_upstream_contract"
            )
        self._session_id = session_id
        self._transport.request_json(
            method="POST",
            path=path,
            payload={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            headers=self._session_headers(None),
            service_bearer_secret_ref=secret_ref,
        )
        self._initialized_for = binding

    @staticmethod
    def _base_headers() -> dict[str, str]:
        return {
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": _PROTOCOL_VERSION,
        }

    def _session_headers(
        self,
        supplied: object,
    ) -> dict[str, str]:
        headers = self._base_headers()
        if isinstance(supplied, Mapping):
            headers.update(
                {
                    str(name): str(value)
                    for name, value in supplied.items()
                }
            )
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        return headers


__all__ = [
    "JsonHttpTransportPort",
    "McpStreamableHttpTransport",
]
