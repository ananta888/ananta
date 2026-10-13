"""One owner-authorized HTTP read; never a model process or task orchestrator."""

import json
import sys

from worker.meet_media.companion_execution_ports import decode
from worker.meet_media.companion_task_grant_client import AssignedCompanionGrantClient
from worker.meet_media.companion_task_tool_client import AssignedCompanionToolClient


def main():
    try:
        raw = sys.stdin.buffer.readline(24_001)
        if not raw or len(raw) > 24_000 or not raw.endswith(b"\n"):
            return 2
        value = decode(raw)
        if (not isinstance(value, dict) or set(value) != {"assignment", "endpoint", "key_hex", "tool", "arguments"}
                or not isinstance(value["key_hex"], str) or len(value["key_hex"]) > 512):
            return 2
        grants = AssignedCompanionGrantClient(value["assignment"], value["endpoint"], bytes.fromhex(value["key_hex"]))
        result = AssignedCompanionToolClient(grants).call(value["tool"], value["arguments"])
        sys.stdout.write(json.dumps(result, ensure_ascii=False, allow_nan=False) + "\n")
        sys.stdout.flush()
        return 0
    except Exception:  # noqa: BLE001 -- never print credentials, request data or private HTTP errors
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
