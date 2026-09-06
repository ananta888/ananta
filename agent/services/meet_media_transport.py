"""Private-container HTTP adapter; no redirect, proxy or caller-selected URL."""

import hmac
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from agent.services.meet_contract import MeetError
from agent.services.meet_media_result import validate_response_budget as validate_response_budget
from agent.services.meet_media_result import validate_result as validate_result
from agent.services.private_container_network_policy import pin_private_container_address
from worker.meet_media.contract import MAX_RESULT_BYTES, encode, signature


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise MeetError("meet_worker_redirect_denied", 502)


class HttpMediaWorker:
    def __init__(self, endpoint, key):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.port is None
            or parsed.path != "/v1/turns"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("meet_worker_endpoint_invalid")
        self.endpoint, self.key = endpoint, key

    def execute(self, turn):
        # Pin DNS after rejecting public, loopback, metadata and mixed resolutions.
        parsed = urlsplit(self.endpoint)
        address = pin_private_container_address(parsed.hostname, parsed.port)
        host = f"[{address}]" if ":" in address else address
        body = encode(turn)
        request = urllib.request.Request(
            f"http://{host}:{parsed.port}{parsed.path}",
            body,
            {
                "Content-Type": "application/json",
                "Host": parsed.netloc,
                "X-Ananta-Task-Signature": signature(self.key, body),
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        try:
            with opener.open(request, timeout=max(0.1, turn["deadline"] - time.time())) as response:
                raw = response.read(MAX_RESULT_BYTES + 1)
                supplied_signature = response.headers.get("X-Ananta-Result-Signature", "")
            if len(raw) > MAX_RESULT_BYTES:
                raise MeetError("meet_worker_result_too_large", 502)
            if not hmac.compare_digest(signature(self.key, b"result-v1\0" + raw), supplied_signature):
                raise MeetError("meet_worker_result_unauthorized", 502)
            result = validate_result(json.loads(raw))
            validate_response_budget(turn, result)
            if ("meeting" in turn) != ("meeting" in result) or (
                "meeting" in turn and result["meeting"]["room_id"] != turn["meeting"]["room_id"]
            ):
                raise MeetError("meet_worker_publication_mismatch", 502)
            return result
        except MeetError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            raise MeetError("meet_worker_unavailable", 503) from None
