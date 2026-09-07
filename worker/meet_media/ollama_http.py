"""Fixed operator endpoint and bounded JSON reads; no redirects or proxy fallback."""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urlsplit

from worker.meet_media.persona_http import read_bounded


class OllamaJsonPort(Protocol):
    def chat(self, payload: dict) -> dict: ...
    def models(self) -> dict: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, response, *_args, **_kwargs):
        response.close()
        raise ValueError("meet_llm_redirect_denied")


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("meet_llm_duplicate_json_key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("meet_llm_nonfinite_json")


def _endpoint(value):
    try:
        if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or "?" in value
            or "#" in value
            or "\\" in value
            or parsed.port is not None
            and not 1 <= parsed.port <= 65535
        ):
            raise ValueError()
        return value.rstrip("/")
    except ValueError:
        raise ValueError("meet_llm_endpoint_invalid") from None


class OllamaHttp:
    def __init__(self, endpoint=None, *, opener=None, clock=time.monotonic):
        self.endpoint = _endpoint(
            os.environ.get("MEET_OLLAMA_URL", "http://meet-ollama:11434") if endpoint is None else endpoint
        )
        self.opener = (
            opener
            if opener is not None
            else urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        )
        self.clock = clock

    def chat(self, payload):
        return self._json("/api/chat", payload, 60)

    def models(self):
        return self._json("/api/ps", None, 5)

    def _json(self, path, payload, budget):
        deadline = self.clock() + budget
        try:
            body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
            request = urllib.request.Request(self.endpoint + path, body, {"Content-Type": "application/json"})
            with self.opener.open(request, timeout=budget) as response:
                # Explicitly reject non-success statuses even for injected ports.
                if response.status != 200:
                    raise ValueError()
                raw = read_bounded(response, maximum=65536, deadline=deadline)
            result = json.loads(raw, object_pairs_hook=_json_object, parse_constant=_invalid_constant)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except Exception as error:
            if isinstance(error, urllib.error.HTTPError):
                try:
                    error.close()
                except Exception:
                    pass  # Cleanup diagnostics must not replace the redacted failure.
            raise ValueError("meet_llm_transport_failed") from None
