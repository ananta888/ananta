"""One parent-authorized model lifecycle request, with no task orchestration."""

import json
import sys

from ananta_contracts.meet_companion_task_model import HELPER_INPUT_LIMIT
from worker.meet_media.companion_execution_ports import decode
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
from worker.meet_media.companion_task_model_client import AssignedCompanionModelClient


def main():
    try:
        raw = sys.stdin.buffer.readline(HELPER_INPUT_LIMIT + 1)
        if not raw or len(raw) > HELPER_INPUT_LIMIT or not raw.endswith(b"\n"):
            return 2
        value = decode(raw)
        if (not isinstance(value, dict) or set(value) != {"assignment", "endpoint", "key_hex", "action", "payload"}
                or not isinstance(value["key_hex"], str) or len(value["key_hex"]) > 512):
            return 2
        grants = AssignedCompanionGrantClient(value["assignment"], value["endpoint"], bytes.fromhex(value["key_hex"]))
        result = AssignedCompanionModelClient(grants).call(value["action"], value["payload"])
        sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        sys.stdout.flush()
        return 0
    except Exception:  # noqa: BLE001 -- redact credentials, room input and private transport diagnostics
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
