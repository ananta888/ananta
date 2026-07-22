"""Shared fail-closed primitives for SFU broadcast release gates."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any, Iterable, Mapping

from scripts.sfu_broadcast_gate_common import (
    SfuBroadcastGateError,
    atomic_write_report,
    canonical_sha256,
    read_bounded_json,
    scan_content_free_document,
)

SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
ACTIVE_PARENT_STAGES = frozenset(
    {"single_pair_opt_in", "trusted_small_group", "bounded_pilot", "general_opt_in"}
)


def is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def parent_activation_reasons(parent: Mapping[str, Any] | None) -> tuple[str, ...]:
    if parent is None:
        return ("parent_evidence_missing",)
    reasons: set[str] = set()
    if parent.get("decision") != "go":
        reasons.add("parent_decision_not_go")
    stage = parent.get("rollout_stage")
    if stage == "observe_only":
        reasons.add("parent_rollout_observe_only")
    elif stage not in ACTIVE_PARENT_STAGES:
        reasons.add("parent_rollout_stage_invalid")
    if not is_sha256(parent.get("source_sha256")):
        reasons.add("parent_source_digest_invalid")
    return tuple(sorted(reasons))


def validate_bindings(
    document: Mapping[str, Any],
    *,
    expected_config_sha256: str,
    required_image_ids: Iterable[str],
    lockfile_required: bool = False,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    reasons: set[str] = set()
    bindings = document.get("bindings")
    if not isinstance(bindings, Mapping):
        return ("evidence_bindings_missing",), {}
    source_sha256 = bindings.get("source_sha256")
    config_sha256 = bindings.get("config_sha256")
    infrastructure_sha256 = bindings.get("infrastructure_sha256")
    if not is_sha256(source_sha256):
        reasons.add("evidence_source_digest_invalid")
    if config_sha256 != expected_config_sha256:
        reasons.add("evidence_config_digest_mismatch")
    if not is_sha256(infrastructure_sha256):
        reasons.add("evidence_infrastructure_digest_invalid")
    if lockfile_required and not is_sha256(bindings.get("lockfile_sha256")):
        reasons.add("evidence_lockfile_digest_invalid")
    image_digests = bindings.get("image_digests")
    required = set(required_image_ids)
    if not isinstance(image_digests, Mapping):
        reasons.add("evidence_image_digests_missing")
        normalized_images: dict[str, str] = {}
    else:
        normalized_images = {
            str(name): str(digest)
            for name, digest in image_digests.items()
            if isinstance(name, str) and isinstance(digest, str)
        }
        if set(normalized_images) != required:
            reasons.add("evidence_image_digest_scope_mismatch")
        if any(not is_sha256(value) for value in normalized_images.values()):
            reasons.add("evidence_image_digest_invalid")
    normalized = {
        "source_sha256": source_sha256 if is_sha256(source_sha256) else None,
        "config_sha256": config_sha256 if is_sha256(config_sha256) else None,
        "infrastructure_sha256": infrastructure_sha256 if is_sha256(infrastructure_sha256) else None,
        "image_digests": dict(sorted(normalized_images.items())),
    }
    if lockfile_required:
        lockfile = bindings.get("lockfile_sha256")
        normalized["lockfile_sha256"] = lockfile if is_sha256(lockfile) else None
    return tuple(sorted(reasons)), normalized


def content_reasons(*documents: Mapping[str, Any]) -> tuple[str, ...]:
    reasons: set[str] = set()
    for document in documents:
        reasons.update(scan_content_free_document(document))
    return tuple(sorted(reasons))


def validate_exceptions(
    value: Any,
    *,
    as_of: date,
    allowed_scopes: set[str],
) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ("exception_inventory_invalid",)
    reasons: set[str] = set()
    seen: set[tuple[str, str]] = set()
    required = {
        "rule_id", "scope", "owner", "rationale", "expires_on",
        "compensating_control", "approval_key_id", "approval_digest",
        "approval_signature_verified",
    }
    for item in value:
        if not isinstance(item, Mapping) or set(item) != required:
            reasons.add("exception_contract_invalid")
            continue
        key = (str(item.get("scope")), str(item.get("rule_id")))
        if key in seen:
            reasons.add("exception_duplicate")
        seen.add(key)
        if key[0] not in allowed_scopes:
            reasons.add("exception_scope_invalid")
        if not all(
            isinstance(item.get(field), str) and bool(str(item[field]).strip())
            for field in ("owner", "rationale", "compensating_control")
        ):
            reasons.add("exception_justification_incomplete")
        if not IDENTIFIER_RE.fullmatch(str(item.get("approval_key_id") or "")):
            reasons.add("exception_approval_key_invalid")
        if not is_sha256(item.get("approval_digest")):
            reasons.add("exception_approval_digest_invalid")
        if item.get("approval_signature_verified") is not True:
            reasons.add("exception_approval_unverified")
        try:
            expires = date.fromisoformat(str(item.get("expires_on")))
        except ValueError:
            reasons.add("exception_expiry_invalid")
        else:
            if expires < as_of:
                reasons.add("exception_expired")
    return tuple(sorted(reasons))


def build_report(
    *,
    schema: str,
    gate_id: str,
    reasons: Iterable[str],
    bindings: Mapping[str, Any],
    summary: Mapping[str, Any],
    parent: Mapping[str, Any] | None,
) -> dict[str, Any]:
    technical_reasons = tuple(sorted(set(reasons)))
    activation_blockers = parent_activation_reasons(parent)
    report = {
        "schema": schema,
        "gate_id": gate_id,
        "status": "passed" if not technical_reasons else "failed",
        "activation_allowed": not technical_reasons and not activation_blockers,
        "reason_codes": list(technical_reasons),
        "activation_blockers": list(activation_blockers),
        "bindings": dict(bindings),
        "summary": dict(summary),
    }
    content = scan_content_free_document(report)
    if content:
        raise SfuBroadcastGateError(content[0])
    return report


def unavailable_report(
    *,
    schema: str,
    gate_id: str,
    reason: str,
    config: Mapping[str, Any],
    parent: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return build_report(
        schema=schema,
        gate_id=gate_id,
        reasons=(reason,),
        bindings={"config_sha256": canonical_sha256(config)},
        summary={"external_evidence": "unavailable"},
        parent=parent,
    )


__all__ = [
    "IDENTIFIER_RE", "atomic_write_report", "build_report", "canonical_sha256",
    "content_reasons", "is_sha256", "parse_utc", "parent_activation_reasons",
    "read_bounded_json", "unavailable_report", "validate_bindings",
    "validate_exceptions",
]
