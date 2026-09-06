"""Read-only Hub lease checks. A worker cannot refresh its own authority."""

import hmac
import json
import os
import secrets
import time
import urllib.request
from urllib.parse import urlsplit

from ananta_contracts.meet_lease import lease_response_signature
from worker.meet_media.contract import encode, load_key, signature
from worker.meet_media.persona_http import read_bounded


class HubLeaseGuard:
    def __init__(self, task_id, lease_id, *, deadline=None):
        self.url = os.environ.get("MEET_HUB_LEASE_URL", "")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path != "/api/meet/v1/internal/lease"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("meet_hub_lease_endpoint_required")
        self.key = load_key(os.environ["MEET_WORKER_KEY_FILE"])
        self.task_id, self.lease_id = task_id, lease_id
        self.deadline = (
            time.monotonic() + min(120, deadline - time.time()) if deadline is not None else time.monotonic() + 120
        )

    def require(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise ValueError("meet_lease_redirect_denied")

        remaining = min(3, self.deadline - time.monotonic())
        if remaining <= 0:
            raise ValueError("meet_hub_lease_revoked_or_unavailable")
        deadline = time.monotonic() + remaining
        body = encode({"task_id": self.task_id, "lease_id": self.lease_id, "nonce": secrets.token_hex(16)})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(
            self.url,
            body,
            {
                "Content-Type": "application/json",
                "X-Ananta-Task-Signature": signature(self.key, body),
            },
        )
        try:
            with opener.open(request, timeout=remaining) as response:
                raw = read_bounded(response, maximum=512, deadline=deadline)
                signed = response.headers.get("X-Ananta-Lease-Signature", "")
                protocol = response.headers.get("X-Ananta-Lease-Protocol", "")
            if protocol != "ananta.meet-lease.v2" or not hmac.compare_digest(
                lease_response_signature(self.key, body, raw), signed
            ):
                raise ValueError()
            result = json.loads(raw)
            if (
                len(raw) > 512
                or not isinstance(result, dict)
                or set(result) != {"allowed"}
                or result["allowed"] is not True
            ):
                raise ValueError()
        except Exception:
            raise ValueError("meet_hub_lease_revoked_or_unavailable") from None
