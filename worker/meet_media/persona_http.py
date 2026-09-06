"""Bounded signed HTTP primitives; domains and request digests prevent cross-use."""

import hashlib
import hmac
import json
import time
import urllib.request

from worker.meet_media.contract import encode, signature


def request_signature(key, domain, raw):
    return signature(key, domain + b"/request\0" + raw)


def result_signature(key, domain, request_raw, result_raw):
    return signature(key, domain + b"/result\0" + hashlib.sha256(request_raw).digest() + result_raw)


def read_bounded(stream, *, maximum, deadline, length=None):
    result = bytearray()
    while length is None or len(result) < length:
        if time.monotonic() >= deadline:
            raise ValueError("persona_http_deadline_exceeded")
        # read1 makes at most one underlying read, so a slow trickle cannot hold
        # a whole multi-MiB read past the absolute deadline.
        chunk = stream.read1(min(65536, maximum + 1 - len(result), length - len(result) if length else maximum + 1))
        if not chunk:
            break
        result.extend(chunk)
        if len(result) > maximum:
            raise ValueError("persona_http_body_too_large")
    if time.monotonic() >= deadline or (length is not None and len(result) != length):
        raise ValueError("persona_http_body_incomplete_or_expired")
    return bytes(result)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise ValueError("persona_http_redirect_denied")


def signed_post(endpoint, key, domain, payload, *, maximum, deadline, host=None):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("persona_http_deadline_exceeded")
    raw = encode(payload)
    headers = {"Content-Type": "application/json", "X-Ananta-Persona-Signature": request_signature(key, domain, raw)}
    if host:
        headers["Host"] = host
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(urllib.request.Request(endpoint, raw, headers), timeout=min(10, remaining)) as response:
            result = read_bounded(response, maximum=maximum, deadline=deadline)
            supplied = response.headers.get("X-Ananta-Persona-Result-Signature", "")
        if not hmac.compare_digest(supplied, result_signature(key, domain, raw, result)):
            raise ValueError("persona_http_result_unauthorized")
        return json.loads(result)
    except Exception:
        raise ValueError("persona_http_unavailable_or_unauthorized") from None
