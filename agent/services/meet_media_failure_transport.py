"""Verify only the new closed Worker failure contract; never forward raw errors."""

import time

from agent.services.meet_contract import MeetError
from ananta_contracts.meet_media_failures import verify_failure
from worker.meet_media.persona_http import read_bounded


def worker_failure(error, *, key, request_body):
    try:
        with error:
            supplied = error.headers.get("X-Ananta-Media-Failure-Signature", "")
            if error.code != 503 or not supplied:
                raise ValueError()
            raw = read_bounded(error, maximum=1024, deadline=time.monotonic() + 3)
        code = verify_failure(key, request_body, raw, supplied)
    except (OSError, ValueError, TypeError):
        return MeetError("meet_worker_unavailable", 503)
    return MeetError(code, 503)
