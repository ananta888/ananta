"""Closed least-privilege projection of a Hub-reserved evidence run."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

_RUN_ID = re.compile(r"^RUN_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$")
_SOURCE_ID = re.compile(r"^SRC_[A-Za-z0-9][A-Za-z0-9_.:-]{0,187}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = frozenset(
    {
        "schema",
        "run_id",
        "task_id",
        "assignment_id",
        "dispatch_lease_id",
        "source_ids",
        "evidence_scope",
        "binding_digest",
        "projection_digest",
    }
)


class HubEvidenceAssignmentError(ValueError):
    pass


def _digest(value: Mapping[str, Any]) -> str:
    unsigned = {key: value[key] for key in value if key != "projection_digest"}
    return hashlib.sha256(
        json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def build_hub_evidence_assignment(
    *,
    run_id: str,
    task_id: str,
    assignment_id: str,
    dispatch_lease_id: str,
    source_ids: Sequence[str],
    evidence_scope: str,
    binding_digest: str,
) -> dict[str, Any]:
    projection: dict[str, Any] = {
        "schema": "ananta.hub-evidence-assignment.v1",
        "run_id": str(run_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "assignment_id": str(assignment_id or "").strip(),
        "dispatch_lease_id": str(dispatch_lease_id or "").strip(),
        "source_ids": sorted(str(value or "").strip() for value in source_ids),
        "evidence_scope": str(evidence_scope or "").strip(),
        "binding_digest": str(binding_digest or "").strip(),
    }
    projection["projection_digest"] = _digest(projection)
    return validate_hub_evidence_assignment(projection)


def validate_hub_evidence_assignment(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise HubEvidenceAssignmentError("hub_evidence_assignment_fields_invalid")
    projection = dict(value)
    sources = projection.get("source_ids")
    if (
        projection.get("schema") != "ananta.hub-evidence-assignment.v1"
        or _RUN_ID.fullmatch(str(projection.get("run_id") or "")) is None
        or any(
            not str(projection.get(field) or "").strip()
            for field in ("task_id", "assignment_id", "dispatch_lease_id")
        )
        or not isinstance(sources, list)
        or not sources
        or sources != sorted(set(sources))
        or any(_SOURCE_ID.fullmatch(str(source)) is None for source in sources)
        or projection.get("evidence_scope")
        not in {"test", "local", "external", "production"}
        or _DIGEST.fullmatch(str(projection.get("binding_digest") or "")) is None
    ):
        raise HubEvidenceAssignmentError("hub_evidence_assignment_binding_invalid")
    actual = str(projection.get("projection_digest") or "")
    expected = _digest(projection)
    if _DIGEST.fullmatch(actual) is None or not hmac.compare_digest(actual, expected):
        raise HubEvidenceAssignmentError("hub_evidence_assignment_digest_mismatch")
    return projection


__all__ = [
    "HubEvidenceAssignmentError",
    "build_hub_evidence_assignment",
    "validate_hub_evidence_assignment",
]
