"""Execution helper for one parent-owned companion conversation, no task queue."""

import base64
import json
import os
import sys

from ananta_contracts.meet_companion_task_grant import validate_model_execution
from worker.meet_media.companion_execution_ports import EXECUTION_REQUEST_LIMIT, CompanionExecutionPorts, decode


def main():
    from worker.meet_media.companion import generate_reply, synthesize_pcm

    unset = object()
    selected, conversation = unset, None

    while True:
        line = sys.stdin.buffer.readline(EXECUTION_REQUEST_LIMIT + 1)
        if not line:
            return 0
        try:
            if len(line) > EXECUTION_REQUEST_LIMIT or not line.endswith(b"\n"):
                return 2
            request = decode(line)
            if (not isinstance(request, dict)
                    or set(request) not in ({"kind", "text"}, {"kind", "text", "model_execution"})):
                return 2
            kind, text = request["kind"], request["text"]
            maximum = 2000 if kind == "answer" else 450
            if kind not in {"answer", "speech"} or not isinstance(text, str) or not 0 < len(text) <= maximum:
                return 2
            if kind == "answer":
                profile = validate_model_execution(request["model_execution"]) if "model_execution" in request else None
                if selected is unset:
                    selected = profile
                    if profile is not None:
                        from worker.meet_media.companion_assigned_dialog import AssignedCompanionDialog

                        os.environ.update({"MEET_LLM_BACKEND": profile["backend"], "MEET_LLM_MODEL": profile["model"]})
                        conversation = AssignedCompanionDialog()
                elif profile != selected:
                    return 2
                result = (conversation.answer(text, CompanionExecutionPorts(sys.stdin.buffer, sys.stdout.buffer,
                            allowed={"tool", "model"})) if conversation is not None else generate_reply(text))
                if not isinstance(result, str) or not 0 < len(result) <= 450:
                    return 2
            else:
                if "model_execution" in request:
                    return 2
                pcm = synthesize_pcm(text)
                if not isinstance(pcm, bytes) or not pcm or len(pcm) > 1_940_400 or len(pcm) % 2:
                    return 2
                result = base64.b64encode(pcm).decode("ascii")
            sys.stdout.write(json.dumps({"kind": kind, "result": result}, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
