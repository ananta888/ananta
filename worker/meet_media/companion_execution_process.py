"""Typed model/TTS adapter over the shared cancellable local pipe."""

import base64
import binascii
import sys

from ananta_contracts.meet_companion_task_grant import validate_model_execution
from worker.meet_media.companion_execution_ports import EXECUTION_REQUEST_LIMIT
from worker.meet_media.companion_process_transport import CompanionExecutionError, CompanionProcessTransport
from worker.meet_media.companion_task_controls import CompanionSourceInterrupted

MAX_RESULT_BYTES = 2_600_000
MAX_PCM_BYTES = 1_940_400


class CompanionExecutionProcess:
    def __init__(self, *, command=None, environment=None, timeout_seconds=120, ports=None, model_profile=None):
        self._model_profile = validate_model_execution(model_profile) if model_profile is not None else None
        if self._model_profile is not None and set(ports or {}) != {"tool", "model"}:
            raise ValueError("meet_companion_assigned_model_ports_required")
        command = command or (sys.executable, "-m", "worker.meet_media.companion_execution_runtime")
        self._transport = CompanionProcessTransport(command, environment=environment, timeout_seconds=timeout_seconds,
                                                    ports=ports, input_limit=EXECUTION_REQUEST_LIMIT)
        self._speech_options = dict(command=command, environment=environment, timeout_seconds=timeout_seconds,
                                    input_limit=EXECUTION_REQUEST_LIMIT)
        self._speech_transport = None
        self._closed = False

    @property
    def _process(self):
        return self._transport.process

    def call(self, kind, text, *, cooperate):
        maximum = 2000 if kind == "answer" else 450
        if kind not in {"answer", "speech"} or not isinstance(text, str) or not text.strip() or len(text) > maximum:
            raise ValueError("meet_companion_execution_request_invalid")
        if self._closed:
            raise CompanionExecutionError("meet_companion_execution_closed")
        try:
            transport = self._transport
            if kind == "speech" and self._model_profile is not None:
                # An optional utterance owns no persistent dialog state or Hub
                # inference ports. Retiring it must preserve the current model.
                self._speech_transport = CompanionProcessTransport(**self._speech_options)
                transport = self._speech_transport
            request = {"kind": kind, "text": text}
            if kind == "answer" and self._model_profile is not None:
                request["model_execution"] = self._model_profile
            result = transport.exchange(request, cooperate=cooperate, allow_ports=kind == "answer")
            if (not isinstance(result, dict) or set(result) != {"kind", "result"}
                    or result["kind"] != kind or not isinstance(result["result"], str)):
                raise CompanionExecutionError("meet_companion_execution_protocol_invalid")
            if kind == "answer":
                if not 0 < len(result["result"]) <= 450:
                    raise CompanionExecutionError("meet_companion_execution_protocol_invalid")
                return result["result"]
            try:
                pcm = base64.b64decode(result["result"], validate=True)
            except (ValueError, binascii.Error):
                raise CompanionExecutionError("meet_companion_execution_protocol_invalid") from None
            if not pcm or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
                raise CompanionExecutionError("meet_companion_execution_protocol_invalid")
            return pcm
        except CompanionSourceInterrupted:
            if kind != "speech" or self._model_profile is None:
                self.close()
            raise
        except BaseException:
            self.close()
            raise
        finally:
            if self._speech_transport is not None:
                self._speech_transport.close()
                self._speech_transport = None

    def close(self):
        self._closed = True
        self._transport.close()
        if self._speech_transport is not None:
            self._speech_transport.close()
