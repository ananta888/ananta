"""Read-only Hub lease checks. A worker cannot refresh its own authority."""

import hmac
import json
import os
import urllib.request
from urllib.parse import urlsplit

from worker.meet_media.contract import encode, load_key, signature


class HubLeaseGuard:
    def __init__(self, task_id, lease_id):
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
        self.body = encode({"task_id": task_id, "lease_id": lease_id})

    def require(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise ValueError("meet_lease_redirect_denied")

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(
            self.url,
            self.body,
            {
                "Content-Type": "application/json",
                "X-Ananta-Task-Signature": signature(self.key, self.body),
            },
        )
        try:
            with opener.open(request, timeout=3) as response:
                raw = response.read(513)
                signed = response.headers.get("X-Ananta-Lease-Signature", "")
            if not hmac.compare_digest(signature(self.key, b"lease-v1\0" + raw), signed):
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
