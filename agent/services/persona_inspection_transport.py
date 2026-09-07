"""Closed inspection transport to a pinned private worker; no Hub decoding."""

import base64
import hashlib
import time
from urllib.parse import urlsplit

from worker.meet_media.persona_http import signed_post


class HttpPersonaInspectionWorker:
    def __init__(self, endpoint, key, *, wire, resolve_address):
        self.wire, self.resolve_address = wire, resolve_address
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.port is None
            or parsed.username
            or parsed.password
            or parsed.path != wire.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("persona_worker_endpoint_invalid")
        self.endpoint, self.key = parsed, key

    def execute(self, assignment, content, media_type):
        self.wire.validate_assignment(assignment, time.time())
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= self.wire.input_limit
            or hashlib.sha256(content).hexdigest() != assignment["source_sha256"]
            or media_type not in self.wire.media_types
        ):
            raise ValueError("persona_worker_input_invalid")
        deadline = time.monotonic() + assignment["deadline"] - time.time()
        parsed = self.endpoint
        address = self.resolve_address(parsed.hostname, parsed.port)
        host = f"[{address}]" if ":" in address else address
        result = signed_post(
            f"http://{host}:{parsed.port}{parsed.path}",
            self.key,
            self.wire.domain,
            {"assignment": assignment, "media_type": media_type, "content": base64.b64encode(content).decode()},
            maximum=self.wire.result_limit,
            deadline=deadline,
            host=parsed.netloc,
        )
        self.wire.validate_assignment(assignment, time.time())
        if (
            not isinstance(result, dict)
            or set(result) != {"task_id", "lease_id", self.wire.kind}
            or (result["task_id"], result["lease_id"]) != (assignment["task_id"], assignment["lease_id"])
        ):
            raise ValueError("persona_worker_result_binding_invalid")
        return self.wire.decode_inspection(result[self.wire.kind], assignment["source_sha256"])
