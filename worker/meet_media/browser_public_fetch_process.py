"""Cancellable public fetch, using the existing bounded trusted-process port."""

import json
import math
import sys
import time
from pathlib import Path

from ananta_contracts.browser_public_fetch import MAX_FETCH_WIRE_BYTES, decode_document, validate_fetch_request
from ananta_contracts.persona_inspection_wire import parse_inspection_json
from voice_runtime.preprocessing.audio_decode import BoundedSubprocessRunner


class PublicDocumentFetch:
    def __init__(self, *, require_current, runner=None):
        self.require_current = require_current
        self.runner = runner if runner is not None else BoundedSubprocessRunner()

    def fetch(self, request, *, deadline):
        request = validate_fetch_request(request)
        now = time.monotonic()
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or not now < deadline <= now + 30:
            raise ValueError("browser_public_fetch_expired")
        self.require_current()
        try:
            result = self.runner.run(
                [sys.executable, "-m", "worker.meet_media.browser_public_fetch_child"],
                input_payload=json.dumps(request, separators=(",", ":")).encode("ascii"),
                max_stdout_bytes=MAX_FETCH_WIRE_BYTES,
                timeout_seconds=min(3, deadline - time.monotonic()),
                cwd=Path(__file__).resolve().parents[2],
                cancellation_check=self.require_current,
            )
            self.require_current()
            if result.returncode != 0 or time.monotonic() >= deadline:
                raise ValueError()
            return decode_document(parse_inspection_json(result.stdout, maximum=MAX_FETCH_WIRE_BYTES))
        except Exception:
            raise ValueError("browser_public_fetch_failed") from None
