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
from agent.services.meet_worker_response_body import read_worker_body
from agent.services.private_container_network_policy import pin_private_container_address
from worker.meet_media.contract import MAX_RESULT_BYTES, encode, signature


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise MeetError("meet_worker_redirect_denied", 502)


def _read_media_result(response, deadline):
    try:
        return read_worker_body(response, maximum=MAX_RESULT_BYTES, deadline=deadline)
    except ValueError as error:
        if str(error) == "persona_http_body_too_large":
            raise MeetError("meet_worker_result_too_large", 502) from None
        raise


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

    @property
    def publisher_url(self):
        """Exact registered origin of this operator-configured transport."""
        return f"http://{urlsplit(self.endpoint).netloc}"

    def start_dialog(self, assignment):
        from ananta_contracts.meet_dialog import parse, request_signature, response_signature, validate_assignment

        validate_assignment(assignment, time.time())
        parsed = urlsplit(self.endpoint)
        address = pin_private_container_address(parsed.hostname, parsed.port)
        host = f"[{address}]" if ":" in address else address
        body = encode(assignment)
        request = urllib.request.Request(
            f"http://{host}:{parsed.port}/v1/dialogs",
            body,
            {
                "Content-Type": "application/json",
                "Host": parsed.netloc,
                "X-Ananta-Dialog-Signature": request_signature(self.key, body),
            },
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        deadline = time.monotonic() + 3
        try:
            with opener.open(request, timeout=3) as response:
                raw = read_worker_body(response, maximum=1024, deadline=deadline)
                signed = response.headers.get("X-Ananta-Dialog-Signature", "")
            if not hmac.compare_digest(response_signature(self.key, body, raw), signed):
                raise ValueError()
            result = parse(raw)
            expected = {
                "schema": "ananta.meet-dialog-accepted.v1",
                "task_id": assignment["task_id"],
                "lease_id": assignment["lease_id"],
                "runtime_id": assignment["runtime_id"],
                "status": "accepted",
            }
            if result != expected:
                raise ValueError()
            return result
        except (OSError, ValueError, urllib.error.URLError):
            raise MeetError("meet_dialog_worker_unavailable", 503) from None

    def observe_dialog_resources(self):
        from agent.services.meet_dialog_resources import HttpDialogResources

        return HttpDialogResources(self.publisher_url, self.key).observe()

    def execute(self, turn):
        remaining = turn["deadline"] - time.time()
        if remaining <= 0:
            raise MeetError("meet_worker_unavailable", 503)
        deadline = time.monotonic() + remaining
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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MeetError("meet_worker_unavailable", 503)
            with opener.open(request, timeout=remaining) as response:
                raw = _read_media_result(response, deadline)
                supplied_signature = response.headers.get("X-Ananta-Result-Signature", "")
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
        except urllib.error.HTTPError as error:
            from agent.services.meet_media_failure_transport import worker_failure

            raise worker_failure(error, key=self.key, request_body=body) from None
        except (OSError, ValueError, urllib.error.URLError):
            raise MeetError("meet_worker_unavailable", 503) from None
