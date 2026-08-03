"""Closed contracts for the delegated Organization Track-planning phase."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any

from agent.services.planning_artifact_transition_service import (
    PlanningTransitionError,
)

TRACK_PLANNING_RESULT_SCHEMA = "organization_track_planning_result.v1"
_RESULT_FIELDS = frozenset(
    {
        "schema",
        "payload_digest",
        "category_revision_id",
        "source_category_item_ids",
        "track_candidates",
        "exclusions",
    }
)
_CANDIDATE_FIELDS = frozenset({"artifact_id", "payload"})
_SHA256_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


def required_track_category_item_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the canonical non-deferred Category scope, rejecting ambiguity."""

    item_ids: list[str] = []
    seen: set[str] = set()
    for group in list(payload.get("categories") or []):
        if not isinstance(group, Mapping):
            continue
        for raw_item in list(group.get("items") or []):
            if not isinstance(raw_item, Mapping):
                continue
            item_id = str(raw_item.get("id") or "").strip()
            if not item_id:
                raise PlanningTransitionError("track_planning_category_item_id_missing")
            if item_id in seen:
                raise PlanningTransitionError("track_planning_category_item_id_duplicate")
            seen.add(item_id)
            if str(raw_item.get("status") or "").strip().lower() != "deferred":
                item_ids.append(item_id)
    if not item_ids:
        raise PlanningTransitionError("track_planning_category_scope_empty")
    return tuple(sorted(item_ids))


def track_planning_result_digest(carrier: Mapping[str, Any]) -> str:
    """Digest every result field except the digest field itself."""

    canonical = {key: carrier.get(key) for key in sorted(_RESULT_FIELDS - {"payload_digest"})}
    try:
        encoded = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanningTransitionError("track_planning_result_not_canonical_json") from exc
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def validate_track_planning_result_carrier(
    carrier: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize the closed untrusted Worker result carrier."""

    if set(carrier) != _RESULT_FIELDS or carrier.get("schema") != TRACK_PLANNING_RESULT_SCHEMA:
        raise PlanningTransitionError("track_planning_result_carrier_invalid")
    category_revision_id = validate_track_planning_identifier(
        carrier.get("category_revision_id"),
        reason_code="track_planning_result_carrier_invalid",
    )
    raw_source_ids = carrier.get("source_category_item_ids")
    if not isinstance(raw_source_ids, list) or not 1 <= len(raw_source_ids) <= 100:
        raise PlanningTransitionError("track_planning_result_carrier_invalid")
    source_ids = [
        validate_track_planning_identifier(
            value,
            reason_code="track_planning_result_carrier_invalid",
        )
        for value in raw_source_ids
    ]
    if len(set(source_ids)) != len(source_ids):
        raise PlanningTransitionError("track_planning_result_carrier_invalid")

    raw_candidates = carrier.get("track_candidates")
    if not isinstance(raw_candidates, list) or not 1 <= len(raw_candidates) <= 100:
        raise PlanningTransitionError("track_planning_result_carrier_invalid")
    candidates: list[dict[str, Any]] = []
    artifact_ids: set[str] = set()
    for candidate in raw_candidates:
        if not isinstance(candidate, Mapping) or set(candidate) != _CANDIDATE_FIELDS:
            raise PlanningTransitionError("track_planning_result_carrier_invalid")
        artifact_id = validate_track_planning_identifier(
            candidate.get("artifact_id"),
            reason_code="track_planning_result_carrier_invalid",
        )
        payload = candidate.get("payload")
        if artifact_id in artifact_ids or not isinstance(payload, Mapping) or not payload:
            raise PlanningTransitionError("track_planning_result_carrier_invalid")
        artifact_ids.add(artifact_id)
        candidates.append({"artifact_id": artifact_id, "payload": dict(payload)})

    raw_exclusions = carrier.get("exclusions")
    if not isinstance(raw_exclusions, Mapping) or len(raw_exclusions) > 100:
        raise PlanningTransitionError("track_planning_result_carrier_invalid")
    exclusions: dict[str, str] = {}
    for raw_item_id, raw_reason in raw_exclusions.items():
        item_id = validate_track_planning_identifier(
            raw_item_id,
            reason_code="track_planning_result_carrier_invalid",
        )
        reason = str(raw_reason or "").strip() if isinstance(raw_reason, str) else ""
        if not reason or len(reason) > 2000:
            raise PlanningTransitionError("track_planning_result_carrier_invalid")
        exclusions[item_id] = reason

    normalized = {
        "schema": TRACK_PLANNING_RESULT_SCHEMA,
        "payload_digest": str(carrier.get("payload_digest") or ""),
        "category_revision_id": category_revision_id,
        "source_category_item_ids": source_ids,
        "track_candidates": candidates,
        "exclusions": exclusions,
    }
    if _SHA256_DIGEST.fullmatch(normalized["payload_digest"]) is None:
        raise PlanningTransitionError("track_planning_result_carrier_invalid")
    if normalized["payload_digest"] != track_planning_result_digest(normalized):
        raise PlanningTransitionError("track_planning_result_digest_mismatch")
    return normalized


def validate_track_planning_identifier(value: Any, *, reason_code: str) -> str:
    normalized = str(value or "").strip() if isinstance(value, str) else ""
    if not normalized or len(normalized) > 191 or any(character.isspace() for character in normalized):
        raise PlanningTransitionError(reason_code)
    return normalized


__all__ = [
    "TRACK_PLANNING_RESULT_SCHEMA",
    "required_track_category_item_ids",
    "track_planning_result_digest",
    "validate_track_planning_identifier",
    "validate_track_planning_result_carrier",
]
