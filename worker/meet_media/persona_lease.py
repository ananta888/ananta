"""A worker can check, but never create or refresh, its image task authority."""

import time
import uuid
from urllib.parse import urlsplit

from worker.meet_media.persona_http import signed_post


class PersonaLeaseGuard:
    def __init__(self, endpoint, key, assignment):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path != "/api/persona-media/v1/internal/image-lease"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("persona_hub_lease_endpoint_required")
        self.endpoint, self.key, self.assignment = endpoint, key, assignment
        self.deadline = time.monotonic() + min(20, assignment["deadline"] - time.time())

    def require(self):
        result = signed_post(
            self.endpoint,
            self.key,
            b"persona-lease-v1",
            {"assignment": self.assignment, "nonce": str(uuid.uuid4())},
            maximum=512,
            deadline=min(self.deadline, time.monotonic() + 3),
        )
        if result != {"allowed": True}:
            raise PermissionError("persona_hub_lease_revoked")
