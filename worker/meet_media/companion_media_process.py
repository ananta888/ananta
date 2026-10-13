"""Typed idle/speaking preparation port over an owned cancellable helper."""

import base64
import sys

from worker.meet_media.companion_media_preparation import (
    IDLE_STATES,
    MAX_PCM_BYTES,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    validate_clip,
    validate_clips,
)
from worker.meet_media.companion_process_transport import CompanionExecutionError, CompanionProcessTransport


class CompanionMediaPreparationProcess:
    def __init__(self, *, command=None, environment=None, timeout_seconds=120):
        command = command or (sys.executable, "-m", "worker.meet_media.companion_media_runtime")
        self._transport = CompanionProcessTransport(
            command, environment=environment, timeout_seconds=timeout_seconds,
            input_limit=MAX_REQUEST_BYTES, output_limit=MAX_RESPONSE_BYTES)

    def idle(self, state, *, cooperate):
        if not isinstance(state, str) or state not in IDLE_STATES:
            raise ValueError("meet_avatar_state_invalid")
        return self._exchange("idle", state, cooperate=cooperate)

    def speaking(self, pcm, *, cooperate):
        if not isinstance(pcm, bytes) or not pcm or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
            raise ValueError("meet_companion_media_pcm_invalid")
        return self._exchange("speaking", base64.b64encode(pcm).decode("ascii"), cooperate=cooperate)

    def _exchange(self, kind, value, *, cooperate):
        try:
            receipt = self._transport.exchange({"kind": kind, "value": value}, cooperate=cooperate)
            if not isinstance(receipt, dict) or set(receipt) != {"kind", "result"} or receipt["kind"] != kind:
                raise CompanionExecutionError("meet_companion_media_receipt_invalid")
            return (validate_clip(receipt["result"], repeat="loop") if kind == "idle"
                    else validate_clips(receipt["result"]))
        except BaseException:
            self.close()
            raise

    def close(self):
        self._transport.close()
