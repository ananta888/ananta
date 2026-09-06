"""Image task transport to a pinned private worker; no decoding on the Hub."""

import base64
import hashlib
import time
from urllib.parse import urlsplit

from agent.services.private_container_network_policy import pin_private_container_address
from ananta_contracts.persona_image import MAX_INPUT_BYTES, MAX_RESULT_BYTES, decode_image, validate_assignment
from worker.meet_media.persona_http import signed_post


class HttpPersonaImageWorker:
    def __init__(self, endpoint, key):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.port is None
            or parsed.username
            or parsed.password
            or parsed.path != "/v1/persona-images"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("persona_worker_endpoint_invalid")
        self.endpoint, self.key = parsed, key

    def execute(self, assignment, content, media_type):
        validate_assignment(assignment, time.time())
        if (
            not isinstance(content, bytes)
            or not 0 < len(content) <= MAX_INPUT_BYTES
            or hashlib.sha256(content).hexdigest() != assignment["source_sha256"]
            or media_type not in {"image/png", "image/jpeg"}
        ):
            raise ValueError("persona_worker_input_invalid")
        deadline = time.monotonic() + assignment["deadline"] - time.time()
        parsed = self.endpoint
        address = pin_private_container_address(parsed.hostname, parsed.port)
        host = f"[{address}]" if ":" in address else address
        result = signed_post(
            f"http://{host}:{parsed.port}{parsed.path}",
            self.key,
            b"persona-image-v1",
            {"assignment": assignment, "media_type": media_type, "content": base64.b64encode(content).decode()},
            maximum=MAX_RESULT_BYTES,
            deadline=deadline,
            host=parsed.netloc,
        )
        validate_assignment(assignment, time.time())
        if (
            not isinstance(result, dict)
            or set(result) != {"task_id", "lease_id", "image"}
            or (result["task_id"], result["lease_id"]) != (assignment["task_id"], assignment["lease_id"])
        ):
            raise ValueError("persona_worker_result_binding_invalid")
        return decode_image(result["image"], assignment["source_sha256"])
