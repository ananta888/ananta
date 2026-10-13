"""Consume one Hub-bound read tool; never issue evidence or use legacy assist."""

import hmac
import re
import secrets
import urllib.error
import urllib.request
from copy import deepcopy

from ananta_contracts.meet_companion_task_grant import ASSIGNMENT_FIELDS
from ananta_contracts.meet_companion_task_tool import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    REQUEST_SCHEMA,
    SIGNATURE_HEADER,
    parse,
    request_signature,
    response_signature,
    validate_response,
)
from worker.meet_media.contract import encode
from worker.meet_media.persona_http import read_bounded


class AssignedCompanionToolClient:
    """The validated grant transport fixes endpoint, key and original deadline."""

    def __init__(self, grants):
        self._assignment = {key: deepcopy(grants.assignment[key]) for key in ASSIGNMENT_FIELDS}
        self.endpoint = grants.endpoint.rsplit("/", 1)[0] + "/task-tool"
        self.key, self.clock, self.monotonic = grants.key, grants.clock, grants.monotonic
        self.deadline, self.opener = grants.deadline, grants.opener

    def call(self, tool, arguments):
        started = self.monotonic()
        deadline = min(self.deadline, started + 20)
        if started >= deadline:
            raise ValueError("meet_companion_assignment_expired")
        raw = encode({"schema": REQUEST_SCHEMA, "nonce": secrets.token_hex(16), "sent_at": int(self.clock()),
                      "assignment": deepcopy(self._assignment), "tool": tool, "arguments": deepcopy(arguments)})
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("meet_companion_task_tool_request_oversize")
        request = urllib.request.Request(self.endpoint, raw, headers={"Content-Type": "application/json",
                                                                  SIGNATURE_HEADER: request_signature(self.key, raw)})
        try:
            with self.opener.open(request, timeout=deadline - started) as response:
                body = read_bounded(response, maximum=MAX_RESPONSE_BYTES, deadline=deadline)
                supplied = response.headers.get(SIGNATURE_HEADER, "")
            if (not re.fullmatch(r"[a-f0-9]{64}", supplied)
                    or not hmac.compare_digest(supplied, response_signature(self.key, raw, body))
                    or self.monotonic() >= deadline):
                raise ValueError()
            return validate_response(parse(body), self._assignment, tool)
        except (OSError, urllib.error.URLError, ValueError, TypeError):
            raise ValueError("meet_companion_task_tool_unavailable_or_invalid") from None
