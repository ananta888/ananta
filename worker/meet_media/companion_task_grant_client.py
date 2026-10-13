"""Read signed Hub controls for one closed assignment; no task/identity issuance."""

import hmac
import re
import secrets
import time
import urllib.request
from copy import deepcopy
from urllib.parse import urlsplit

from ananta_contracts.meet_companion_task_grant import (
    ASSIGNMENT_FIELDS,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    REQUEST_SCHEMA,
    SIGNATURE_HEADER,
    request_signature,
    response_signature,
    validate_response,
)
from ananta_contracts.meet_dialog import parse, validate_assignment
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import NoRedirect, read_bounded


class AssignedCompanionGrantClient:
    """HTTP transport only; executing current controls belongs to the runtime."""

    def __init__(self, assignment, endpoint, key, *, opener=None, clock=time.time, monotonic=time.monotonic):
        now = clock()
        self.assignment = deepcopy(validate_assignment(assignment, now))
        if self.assignment.get("companion_execution") is not True:
            raise ValueError("meet_companion_assignment_profile_invalid")
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path != "/api/meet/v1/internal/companion/task-grant" or parsed.query or parsed.fragment):
            raise ValueError("meet_companion_task_endpoint_invalid")
        if not isinstance(key, bytes) or not 32 <= len(key) <= 256:
            raise ValueError("meet_companion_task_key_invalid")
        self.endpoint, self.key, self.clock, self.monotonic = endpoint, key, clock, monotonic
        self.deadline = monotonic() + self.assignment["deadline"] - now
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def read(self):
        started = self.monotonic()
        deadline = min(self.deadline, started + 2.5)
        if started >= deadline:
            raise ValueError("meet_companion_assignment_expired")
        payload = {key: deepcopy(self.assignment[key]) for key in ASSIGNMENT_FIELDS}
        raw = encode({"schema": REQUEST_SCHEMA, "nonce": secrets.token_hex(16),
                      "sent_at": int(self.clock()), "assignment": payload})
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("meet_companion_task_request_oversize")
        request = urllib.request.Request(self.endpoint, raw, headers={
            "Content-Type": "application/json", SIGNATURE_HEADER: request_signature(self.key, raw)})
        try:
            with self.opener.open(request, timeout=deadline - started) as response:
                body = read_bounded(response, maximum=MAX_RESPONSE_BYTES, deadline=deadline)
                supplied = response.headers.get(SIGNATURE_HEADER, "")
            if (not re.fullmatch(r"[a-f0-9]{64}", supplied)
                    or not hmac.compare_digest(supplied, response_signature(self.key, raw, body))
                    or self.monotonic() >= deadline):
                raise ValueError()
            result = validate_response(parse(body), self.assignment, self.clock())
            return result
        except Exception:
            # HTTP errors/transport/JSON may contain tokens or private bodies.
            # No cached authority or legacy grant fallback on any failure.
            raise ValueError("meet_companion_task_grant_unavailable_or_invalid") from None
