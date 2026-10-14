"""Signed closed inference assignments. Identity issuance belongs to the Hub Registry."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import time

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment


def input_digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def profile_digest(model_revision: str) -> str:
    return input_digest({"provider": "jeff_gliformer", "model_revision": model_revision, "isolation": "all"})


def seal_assignment(payload: dict, private_key) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return (
        base64.urlsafe_b64encode(encoded).decode() + "." + base64.urlsafe_b64encode(private_key.sign(encoded)).decode()
    )


def verify_assignment(token: str, public_key, *, body: dict, model_revision: str, now: float | None = None) -> dict:
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 8192:
            raise ValueError("assignment_size")
        encoded, signature = token.split(".")
        raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
        public_key.verify(base64.b64decode(signature, altchars=b"-_", validate=True), raw)
        payload = json.loads(raw)
        if set(payload) != {
            "schema",
            "tenant_id",
            "project_id",
            "evidence",
            "input_digest",
            "model_revision",
            "expires_at",
        }:
            raise ValueError("assignment_fields")
        if payload["schema"] != "ananta.specialist-inference-assignment.v1":
            raise ValueError("assignment_schema")
        epoch = time.time() if now is None else now
        expiry = payload["expires_at"]
        if type(expiry) not in {int, float} or not math.isfinite(expiry) or not epoch < expiry <= epoch + 300:
            raise ValueError("assignment_expired")
        if (
            payload["input_digest"] != input_digest(body)
            or payload["model_revision"] != model_revision
            or not payload["tenant_id"]
            or not payload["project_id"]
        ):
            raise ValueError("assignment_binding")
        validate_hub_evidence_assignment(payload["evidence"])
        return payload
    except Exception as error:
        raise ValueError("specialist_assignment_unverified") from error
