"""Common closed Meet membership/lease validation for distinct backchannels."""

import re

from agent.services.meet_contract import MeetError


def validate_membership(value, scope, issuer, session_id, nonce, now_ms, *, schema):
    expected = {
        "issuer": issuer,
        "subject": "machine:" + getattr(scope, "machine_subject", "ananta"),
        "roomId": scope.room_id,
        "taskId": scope.task_id,
        "tenantId": scope.tenant_id,
        "projectId": scope.project_id,
        "protocolVersion": "v2",
        "runtimeId": scope.runtime_id,
        "hubSessionId": scope.session_id,
        "capabilitySet": ",".join(scope.capabilities),
    }
    if (
        value["schema"] != schema
        or value["nonce"] != nonce
        or value["binding"] != expected
        or value["roomId"] != scope.room_id
    ):
        raise MeetError("meet_authorization_scope_invalid", 502)
    lease = value["lease"]
    if (
        not isinstance(lease, dict)
        or set(lease) != {"schema", "sessionId", "generation", "expiresAt", "absoluteExpiresAt"}
        or lease["schema"] != "ananta.meet-session-lease.v1"
        or lease["sessionId"] != session_id
        or type(lease["generation"]) is not int
        or not 1 <= lease["generation"] <= 512
        or type(lease["expiresAt"]) is not int
        or not now_ms < lease["expiresAt"] <= min(now_ms + 600_000, scope.deadline * 1000)
        or type(lease["absoluteExpiresAt"]) is not int
        or not lease["expiresAt"] <= lease["absoluteExpiresAt"] <= now_ms + 7_200_000
        or not isinstance(value["peerId"], str)
        or not re.fullmatch(r"[a-f0-9]{16}", value["peerId"])
        or type(value["membershipEpoch"]) is not int
        or not 1 <= value["membershipEpoch"] < 2**53
    ):
        raise MeetError("meet_authorization_state_invalid", 502)
