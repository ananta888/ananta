from __future__ import annotations

import json
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, request


@dataclass(frozen=True)
class HermesClientError(Exception):
    code: str
    detail: str
    status_code: int | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True)
class HermesClientConfig:
    base_url: str
    timeout_seconds: float
    default_model: str


class HermesHttpClient:
    """Transport-only client wrapper for Hermes chat completions."""

    def __init__(self, *, config: HermesClientConfig) -> None:
        self.config = config

    def chat_completions(
        self,
        *,
        api_key: str,
        system_message: str,
        user_message: str,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": str(model or self.config.default_model).strip(),
            "messages": [
                {"role": "system", "content": str(system_message)},
                {"role": "user", "content": str(user_message)},
            ],
        }
        if max_tokens is not None:
            payload["max_tokens"] = int(max_tokens)
        if temperature is not None:
            payload["temperature"] = float(temperature)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        return self._post_json(
            f"{self.config.base_url}/v1/chat/completions",
            headers=headers,
            payload=payload,
            timeout_seconds=self.config.timeout_seconds,
        )

    def health(self, *, api_key: str = "") -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return self._get_json(
            f"{self.config.base_url}/health",
            headers=headers,
            timeout_seconds=self.config.timeout_seconds,
        )

    def _post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            raise HermesClientError(
                code=self._map_http_status(exc.code),
                detail="Hermes HTTP request failed",
                status_code=exc.code,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise HermesClientError(code="hermes_timeout", detail="Hermes request timed out") from exc
        except error.URLError as exc:
            raise HermesClientError(code="hermes_connection_error", detail="Hermes connection failed") from exc

    def _get_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        req = request.Request(url=url, headers=headers, method="GET")
        try:
            with request.urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except error.HTTPError as exc:
            raise HermesClientError(
                code=self._map_http_status(exc.code),
                detail="Hermes HTTP request failed",
                status_code=exc.code,
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise HermesClientError(code="hermes_timeout", detail="Hermes request timed out") from exc
        except error.URLError as exc:
            raise HermesClientError(code="hermes_connection_error", detail="Hermes connection failed") from exc

    @staticmethod
    def _map_http_status(status_code: int) -> str:
        if status_code in {401, 403}:
            return "hermes_unauthorized"
        if status_code == 404:
            return "hermes_not_found"
        if status_code == 429:
            return "hermes_rate_limited"
        if status_code >= 500:
            return "hermes_server_error"
        return "hermes_http_error"
