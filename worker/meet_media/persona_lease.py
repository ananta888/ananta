"""Workers check, but never create or refresh, their inspection task authority."""

import time
import uuid
from urllib.parse import urlsplit

from worker.meet_media.persona_http import signed_post


class PersonaLeaseGuard:
    def __init__(self, endpoint, key, assignment, *, kind="image"):
        if kind not in ("image", "video", "voice"):
            raise ValueError("persona_inspection_kind_invalid")
        self.domain = b"persona-lease-v1" if kind == "image" else f"persona-{kind}-lease-v1".encode()
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path != f"/api/persona-media/v1/internal/{kind}-lease"
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
            self.domain,
            {"assignment": self.assignment, "nonce": str(uuid.uuid4())},
            maximum=512,
            deadline=min(self.deadline, time.monotonic() + 3),
        )
        if not isinstance(result, dict) or set(result) != {"allowed"} or result["allowed"] is not True:
            raise PermissionError("persona_hub_lease_revoked")
