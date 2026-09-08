"""Bounded command fingerprints and closed historical start receipts."""

import hashlib
import json
import re
from dataclasses import dataclass

from agent.services.meet_contract import MeetError


def canonical(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise MeetError("meet_dialog_payload_invalid") from None
    if len(raw) > 2048:
        raise MeetError("meet_dialog_payload_invalid")
    return raw


def start_fingerprints(principal, project, parent, key, payload):
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", key):
        raise MeetError("meet_dialog_idempotency_key_invalid")
    scope = [principal.tenant_id, project, principal.subject_id, parent]
    if any(not isinstance(value, str) or len(value) > 512 for value in scope) or not all(scope[:3]):
        raise MeetError("meet_dialog_idempotency_scope_invalid")
    if not isinstance(payload, dict):
        raise MeetError("meet_dialog_payload_invalid")
    return hashlib.sha256(canonical(["meet-dialog-start-v1", *scope, key])).hexdigest(), hashlib.sha256(
        canonical(payload)
    ).hexdigest()


def start_receipt(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "task_id", "session_id", "status"}
        or value["schema"] != "ananta.meet-dialog-start.v1"
        or value["status"] != "connecting"
        or any(
            not isinstance(value[field], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[field])
            for field in ("task_id", "session_id")
        )
    ):
        raise MeetError("meet_dialog_start_receipt_invalid", 503)
    return dict(value)


@dataclass(frozen=True)
class DialogStartClaim:
    scope_key: str
    request_digest: str
    token: str
    created: bool
    state: str
    task_id: str | None = None
    session_id: str | None = None

    def receipt(self):
        return start_receipt(
            {
                "schema": "ananta.meet-dialog-start.v1",
                "task_id": self.task_id,
                "session_id": self.session_id,
                "status": "connecting",
            }
        )
