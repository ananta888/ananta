"""Bounded HTTP transport for the external Webcrawler contract."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class WebcrawlerTransportError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int | None = None) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class WebcrawlerHttpTransportPort(Protocol):
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None,
        timeout: float,
    ) -> Mapping[str, Any]: ...

    def stream_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Iterable[Mapping[str, Any]]: ...


class UrllibWebcrawlerHttpTransport:
    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any] | None,
        timeout: float,
    ) -> Mapping[str, Any]:
        body = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(url, data=body, headers=dict(headers), method=method)
        raw = self._open(request, timeout=timeout)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise WebcrawlerTransportError("webcrawler_response_invalid_json") from exc
        if not isinstance(parsed, Mapping):
            raise WebcrawlerTransportError("webcrawler_response_not_object")
        return parsed

    def stream_sse(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout: float,
    ) -> Iterable[Mapping[str, Any]]:
        request_headers = {**dict(headers), "Accept": "text/event-stream"}
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            response = urlopen(request, timeout=timeout)  # noqa: S310 - configured provider endpoint
        except HTTPError as exc:
            raise WebcrawlerTransportError("webcrawler_http_error", status_code=exc.code) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise WebcrawlerTransportError("webcrawler_unavailable") from exc
        consumed = 0
        try:
            for raw_line in response:
                consumed += len(raw_line)
                if consumed > MAX_RESPONSE_BYTES:
                    raise WebcrawlerTransportError("webcrawler_response_too_large")
                try:
                    line = raw_line.decode("utf-8").strip()
                except UnicodeError as exc:
                    raise WebcrawlerTransportError("webcrawler_stream_chunk_invalid") from exc
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    parsed = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise WebcrawlerTransportError("webcrawler_stream_chunk_invalid") from exc
                if not isinstance(parsed, Mapping):
                    raise WebcrawlerTransportError("webcrawler_stream_chunk_invalid")
                yield parsed
        finally:
            response.close()

    @staticmethod
    def _open(request: Request, *, timeout: float) -> bytes:
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured provider endpoint
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise WebcrawlerTransportError("webcrawler_http_error", status_code=exc.code) from exc
        except (URLError, OSError, TimeoutError) as exc:
            raise WebcrawlerTransportError("webcrawler_unavailable") from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise WebcrawlerTransportError("webcrawler_response_too_large")
        return raw
