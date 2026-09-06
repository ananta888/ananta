"""Private-container HTTP adapter; no redirect, proxy or caller-selected URL."""

import base64
import hmac
import json
import math
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from agent.services.meet_contract import MeetError
from agent.services.private_container_network_policy import pin_private_container_address
from worker.meet_media.contract import MAX_RESULT_BYTES, encode, signature


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise MeetError("meet_worker_redirect_denied", 502)


def validate_result(result):
    fields = {"schema", "task_id", "lease_id", "text", "audio", "video", "duration_seconds", "engines"}
    if (
        not isinstance(result, dict)
        or set(result) not in (fields, fields | {"meeting"})
        or result["schema"] != "ananta.meet-turn-result.v1"
    ):
        raise MeetError("meet_worker_result_invalid", 502)
    if not isinstance(result["text"], str) or not 1 <= len(result["text"].strip()) <= 450:
        raise MeetError("meet_worker_result_invalid", 502)
    duration = result["duration_seconds"]
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 40:
        raise MeetError("meet_worker_result_invalid", 502)
    for field, mime, maximum in (("audio", "audio/wav", 2_000_000), ("video", "video/mp4", 3_500_000)):
        item = result[field]
        if not isinstance(item, dict) or set(item) != {"mime", "base64"} or item["mime"] != mime:
            raise MeetError("meet_worker_media_invalid", 502)
        try:
            decoded = base64.b64decode(item["base64"], validate=True)
        except (ValueError, TypeError):
            raise MeetError("meet_worker_media_invalid", 502) from None
        valid_header = (
            decoded[:4] == b"RIFF" and decoded[8:12] == b"WAVE" if field == "audio" else decoded[4:8] == b"ftyp"
        )
        if not valid_header or not 16 <= len(decoded) <= maximum:
            raise MeetError("meet_worker_media_invalid", 502)
    if result["engines"] != {
        "llm": "ollama",
        "speech": "piper-cuda",
        "video": "procedural-avatar-h264_nvenc",
    }:
        raise MeetError("meet_worker_engines_invalid", 502)
    if "meeting" in result:
        import re

        meeting = result["meeting"]
        if (
            not isinstance(meeting, dict)
            or set(meeting) != {"status", "room_id", "delivery_verified"}
            or meeting["status"] != "published"
            or meeting["delivery_verified"] is not False
            or not isinstance(meeting["room_id"], str)
            or not re.fullmatch(r"room-[a-f0-9]{18}", meeting["room_id"])
        ):
            raise MeetError("meet_worker_publication_invalid", 502)
    return result


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
            if ("meeting" in turn) != ("meeting" in result) or (
                "meeting" in turn and result["meeting"]["room_id"] != turn["meeting"]["room_id"]
            ):
                raise MeetError("meet_worker_publication_mismatch", 502)
            return result
        except MeetError:
            raise
        except (OSError, ValueError, urllib.error.URLError):
            raise MeetError("meet_worker_unavailable", 503) from None
