"""Authenticated read-only private Worker observation, never admission authority."""

import hmac
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from agent.models.meet_role_assignment import publisher_origin
from agent.services.meet_contract import MeetError
from agent.services.meet_media_transport import NoRedirect
from agent.services.meet_worker_response_body import read_worker_body
from agent.services.private_container_network_policy import pin_private_container_address
from ananta_contracts.meet_dialog import parse
from ananta_contracts.meet_dialog_resources import request_signature, response_signature, validate_observation
from worker.meet_media.contract import encode


class HttpDialogResources:
    def __init__(self, publisher, key):
        self.publisher, self.key = publisher_origin(publisher), key

    def observe(self):
        nonce = secrets.token_hex(16)
        body = encode({"schema": "ananta.meet-dialog-resources-query.v1", "nonce": nonce})
        parsed = urlsplit(self.publisher)
        address = pin_private_container_address(parsed.hostname, parsed.port)
        host = f"[{address}]" if ":" in address else address
        request = urllib.request.Request(
            f"http://{host}:{parsed.port}/v1/dialog-resources",
            body,
            {
                "Content-Type": "application/json",
                "Host": parsed.netloc,
                "X-Ananta-Resources-Signature": request_signature(self.key, body),
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        deadline = time.monotonic() + 2
        try:
            with opener.open(request, timeout=2) as response:
                raw = read_worker_body(response, maximum=1024, deadline=deadline)
                supplied = response.headers.get("X-Ananta-Resources-Signature", "")
            if not hmac.compare_digest(response_signature(self.key, body, raw), supplied):
                raise ValueError()
            return validate_observation(parse(raw), nonce)
        except (OSError, ValueError, urllib.error.URLError):
            raise MeetError("meet_dialog_resources_unavailable", 503) from None
