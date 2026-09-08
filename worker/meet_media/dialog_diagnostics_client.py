"""One bounded optional report to the same configured Hub; no control changes."""

import hmac
import math
import re
import secrets
import time
import urllib.request
from urllib.parse import urlsplit

from ananta_contracts.meet_dialog import parse
from ananta_contracts.meet_dialog_diagnostics import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    request_signature,
    response_signature,
    validate_request,
)
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import NoRedirect, read_bounded


def report_terminal(
    url, key, identifiers, observation, assignment_deadline, *, clock=time.monotonic, wall_clock=time.time, opener=None
):
    try:
        endpoint = urlsplit(url)
        if (
            endpoint.scheme not in {"http", "https"}
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.path != "/api/meet/v1/internal/dialog"
            or endpoint.query
            or endpoint.fragment
        ):
            return False
        if type(assignment_deadline) not in {int, float} or not math.isfinite(assignment_deadline):
            return False
        deadline = min(clock() + 1, assignment_deadline + 5)
        if clock() >= deadline:
            return False
        payload = validate_request(
            {
                "schema": "ananta.meet-dialog-diagnostics-request.v1",
                **identifiers,
                "nonce": secrets.token_hex(16),
                "sent_at": int(wall_clock()),
                "observation": observation,
            },
            wall_clock(),
        )
        raw = encode(payload)
        if len(raw) > MAX_REQUEST_BYTES:
            return False
        request = urllib.request.Request(
            url + "/diagnostics",
            raw,
            {"Content-Type": "application/json", "X-Ananta-Dialog-Diagnostics-Signature": request_signature(key, raw)},
        )
        client = (
            opener if opener is not None else urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        )
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        with client.open(request, timeout=remaining) as response:
            lengths = response.headers.get_all("Content-Length", [])
            if (
                response.headers.get_all("Transfer-Encoding", [])
                or len(lengths) != 1
                or not re.fullmatch(r"[0-9]{1,4}", lengths[0])
                or not 0 < int(lengths[0]) <= MAX_RESPONSE_BYTES
            ):
                return False
            result = read_bounded(response, maximum=MAX_RESPONSE_BYTES, deadline=deadline, length=int(lengths[0]))
            signature = response.headers.get("X-Ananta-Dialog-Diagnostics-Signature", "")
        if not hmac.compare_digest(signature, response_signature(key, raw, result)) or clock() >= deadline:
            return False
        value = parse(result)
        return (
            type(value) is dict
            and value
            == {"schema": "ananta.meet-dialog-diagnostics-accepted.v1", "nonce": payload["nonce"], "accepted": True}
            and value["accepted"] is True
        )
    except Exception:
        return False
