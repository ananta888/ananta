"""Request-bound v2 lease responses; nonces are challenges, never evidence IDs."""

import hashlib
import hmac
import re


def validate_lease_request(value):
    if not isinstance(value, dict) or set(value) != {"task_id", "lease_id", "nonce"}:
        raise ValueError("meet_lease_request_invalid")
    for name in ("task_id", "lease_id"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[name]):
            raise ValueError("meet_lease_scope_invalid")
    if not isinstance(value["nonce"], str) or not re.fullmatch(r"[a-f0-9]{32}", value["nonce"]):
        raise ValueError("meet_lease_nonce_invalid")
    return value


def lease_response_signature(key, request_body, response_body):
    message = b"meet-lease-v2\0" + hashlib.sha256(request_body).digest() + response_body
    return hmac.new(key, message, hashlib.sha256).hexdigest()
