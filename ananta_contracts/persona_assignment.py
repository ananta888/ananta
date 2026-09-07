"""Closed image/video dispatch projection; the Hub remains the identity issuer."""

import re

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment

ASSIGNMENT_FIELDS = {
    "schema",
    "task_id",
    "assignment_id",
    "lease_id",
    "tenant_id",
    "project_id",
    "run_id",
    "run_binding_digest",
    "admission_digest",
    "owner_subject",
    "deadline",
    "source_sha256",
    "evidence",
}


def validate_persona_assignment(value, now, *, schema):
    if (
        not isinstance(value, dict)
        or set(value) != ASSIGNMENT_FIELDS
        or schema
        not in ("ananta.persona-image-task.v1", "ananta.persona-video-task.v1", "ananta.persona-voice-task.v1")
        or value["schema"] != schema
    ):
        raise ValueError("persona_assignment_invalid")
    for name in ("task_id", "assignment_id", "lease_id", "tenant_id", "project_id", "owner_subject", "run_id"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[name]):
            raise ValueError("persona_assignment_identity_invalid")
    for name in ("run_binding_digest", "admission_digest", "source_sha256"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[a-f0-9]{64}", value[name]):
            raise ValueError("persona_assignment_digest_invalid")
    if type(value["deadline"]) is not int or not now < value["deadline"] <= now + 20:
        raise ValueError("persona_assignment_expired")
    projection = validate_hub_evidence_assignment(value["evidence"])
    for name, projected in (
        ("task_id", "task_id"),
        ("assignment_id", "assignment_id"),
        ("lease_id", "dispatch_lease_id"),
        ("run_id", "run_id"),
        ("run_binding_digest", "binding_digest"),
    ):
        if value[name] != projection[projected]:
            raise ValueError("persona_assignment_projection_mismatch")
    return value
