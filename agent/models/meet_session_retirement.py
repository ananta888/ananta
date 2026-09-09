"""Closed request-bound absence receipt; never proof of historical membership."""

from agent.models.meet_membership import membership_binding
from agent.services.meet_contract import MeetError


def validate_retirement(value, scope, issuer, session_id, nonce, now_ms):
    # Freshness comes from the current grant, bounded TLS request and exact
    # nonce. A retired lease has no new deadline to authorize or extend.
    del now_ms
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "nonce", "sessionId", "binding", "retired"}
        or value["schema"] != "ananta.meet-session-retired.v1"
        or value["nonce"] != nonce
        or value["sessionId"] != session_id
        or value["binding"] != membership_binding(scope, issuer)
        or value["retired"] is not True
    ):
        raise MeetError("meet_retirement_receipt_invalid", 502)
    return value
