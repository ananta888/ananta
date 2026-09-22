"""Bounded JSON-over-HTTP core shared by the worker's local model transports.

One responsibility: exchange a bounded JSON document with a fixed operator
endpoint, without redirects, proxy fallback, duplicate object keys or
non-finite numbers, and with every failure redacted to a single code so an
upstream body can never reach a log line.

Backend adapters (``ollama_http``, ``openai_http``) own their paths, budgets
and headers; they must not re-implement these guarantees.
"""

import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from worker.meet_media.persona_http import read_bounded

MAX_BODY_BYTES = 65536
MAX_PATH_BYTES = 128
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._~-]+$")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, _request, response, *_args, **_kwargs):
        response.close()
        raise ValueError("meet_llm_redirect_denied")


def json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("meet_llm_duplicate_json_key")
        result[key] = value
    return result


def invalid_constant(_value):
    raise ValueError("meet_llm_nonfinite_json")


def _path_allowed(path, allow_path):
    if path in {"", "/"}:
        return True
    if not allow_path or len(path) > MAX_PATH_BYTES:
        return False
    return all(
        _PATH_SEGMENT.match(segment) and segment not in {".", ".."} for segment in path.strip("/").split("/")
    )


def endpoint(value, *, allow_path=False):
    """Validate one operator-configured base URL.

    ``allow_path`` admits a bounded, traversal-free base path such as ``/v1``;
    OpenAI-compatible servers are addressed that way. Query strings, fragments
    and embedded credentials stay rejected either way, so no part of the room
    text or an operator secret can ride along in the URL.
    """
    try:
        if not isinstance(value, str) or not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
            raise ValueError()
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or not _path_allowed(parsed.path, allow_path)
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


class BoundedJsonClient:
    """Fixed-endpoint JSON exchange; every failure collapses to one code."""

    def __init__(self, base_url, *, opener, clock=time.monotonic, headers=None):
        self.base_url = base_url
        self.opener = opener
        self.clock = clock
        # Copied once: a later environment change must not alter a live client.
        self.headers = dict(headers or {})

    def exchange(self, path, payload, budget):
        deadline = self.clock() + budget
        try:
            body = None if payload is None else json.dumps(payload, allow_nan=False).encode()
            request = urllib.request.Request(
                self.base_url + path, body, {"Content-Type": "application/json", **self.headers}
            )
            with self.opener.open(request, timeout=budget) as response:
                # Explicitly reject non-success statuses even for injected ports.
                if response.status != 200:
                    raise ValueError()
                raw = read_bounded(response, maximum=MAX_BODY_BYTES, deadline=deadline)
            result = json.loads(raw, object_pairs_hook=json_object, parse_constant=invalid_constant)
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
