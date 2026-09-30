"""Closed payload validators and group-preview digests of speech-evidence sync messages."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from ananta_contracts.speech_evidence_sync_primitives import (
    _b64,
    canonical_sha256,
    _closed,
    _control,
    _digest,
    _finite,
    GROUP_PREVIEW_VERSION,
    _identifier,
    _identifiers,
    _integer,
    _integers,
    _mapping,
    MAX_CANDIDATES,
    MAX_CHUNK_CIPHERTEXT_BYTES,
    MAX_CHUNK_PLAINTEXT_BYTES,
    MAX_GROUPS,
    MAX_RETENTION_SECONDS,
    MAX_SEQUENCE,
    MAX_TEXT_CHARS,
    MAX_TOTAL_BYTES,
    OFFER_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SpeechEvidenceProtocolError,
)


def validate_payload(
    message_type: str,
    payload: Mapping[str, Any],
    *,
    protocol_version: str = PROTOCOL_VERSION,
) -> Mapping[str, Any]:
    validator = _PAYLOAD_VALIDATORS.get(message_type)
    if validator is None:
        raise SpeechEvidenceProtocolError("speech_evidence_type_unsupported")
    if message_type == "offer":
        if protocol_version != OFFER_PROTOCOL_VERSION:
            raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_required")
        _offer(payload)
    else:
        validator(payload)
    return dict(payload)


def _inventory(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "inventory_id",
                "root_digest",
                "leaf_count",
                "total_bytes",
                "scope_digest",
                "retention_until_ms",
                "cursor_digest",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("inventory_id"), "speech_evidence_inventory_id_invalid")
    for name in ("root_digest", "scope_digest", "cursor_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    _integer(payload.get("leaf_count"), 0, 100_000, "speech_evidence_leaf_count_invalid")
    _integer(payload.get("total_bytes"), 0, MAX_TOTAL_BYTES, "speech_evidence_total_bytes_invalid")
    _integer(payload.get("retention_until_ms"), 1, MAX_SEQUENCE, "speech_evidence_retention_invalid")


def _diff(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "base_root_digest",
                "target_root_digest",
                "missing_group_ids",
                "changed_group_ids",
                "cursor_digest",
                "complete",
                "total_groups",
            }
        ),
    )
    _control(payload)
    for name in ("base_root_digest", "target_root_digest", "cursor_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    missing = _identifiers(payload.get("missing_group_ids"), MAX_GROUPS, "speech_evidence_groups_invalid")
    changed = _identifiers(payload.get("changed_group_ids"), MAX_GROUPS, "speech_evidence_groups_invalid")
    if set(missing) & set(changed):
        raise SpeechEvidenceProtocolError("speech_evidence_diff_inconsistent")
    if payload.get("complete") is not True and payload.get("complete") is not False:
        raise SpeechEvidenceProtocolError("speech_evidence_diff_complete_invalid")
    total = _integer(payload.get("total_groups"), 0, 100_000, "speech_evidence_group_count_invalid")
    if total < len(missing) + len(changed):
        raise SpeechEvidenceProtocolError("speech_evidence_diff_inconsistent")


def _offer(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "offer_id",
                "stage",
                "inventory_root_digest",
                "direction",
                "purpose",
                "data_classes",
                "fields",
                "retention_seconds",
                "trainer_class",
                "group_ids",
                "group_previews",
                "total_bytes",
                "sender_consent_digest",
                "recipient_consent_digest",
                "scope_digest",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("offer_id"), "speech_evidence_offer_id_invalid")
    if payload.get("stage") not in {"proposal", "acceptance"}:
        raise SpeechEvidenceProtocolError("speech_evidence_offer_stage_invalid")
    if payload.get("direction") not in {"sender_to_receiver", "receiver_to_sender"}:
        raise SpeechEvidenceProtocolError("speech_evidence_direction_invalid")
    if payload.get("purpose") not in {"peer_reconciliation", "speech_dataset_curation"}:
        raise SpeechEvidenceProtocolError("speech_evidence_purpose_invalid")
    _identifiers(payload.get("data_classes"), 8, "speech_evidence_data_classes_invalid")
    _identifiers(payload.get("fields"), 16, "speech_evidence_fields_invalid")
    group_ids = _identifiers(payload.get("group_ids"), MAX_GROUPS, "speech_evidence_groups_invalid")
    previews = _group_previews(payload.get("group_previews"))
    preview_ids = [str(row["group_id"]) for row in previews]
    if set(preview_ids) != set(group_ids) or len(preview_ids) != len(group_ids):
        raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_groups_mismatch")
    if payload.get("trainer_class") not in {"none", "speech_adaptation"}:
        raise SpeechEvidenceProtocolError("speech_evidence_trainer_class_invalid")
    _integer(payload.get("retention_seconds"), 1, MAX_RETENTION_SECONDS, "speech_evidence_retention_invalid")
    total_bytes = _integer(
        payload.get("total_bytes"), 1, MAX_TOTAL_BYTES, "speech_evidence_total_bytes_invalid"
    )
    if sum(int(row["size_bytes"]) for row in previews) != total_bytes:
        raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_size_mismatch")
    for name in (
        "inventory_root_digest",
        "sender_consent_digest",
        "recipient_consent_digest",
        "scope_digest",
    ):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")


def _group_previews(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list) or not value or len(value) > MAX_GROUPS:
        raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_invalid")
    previews: list[Mapping[str, Any]] = []
    group_ids: set[str] = set()
    source_digests: set[str] = set()
    expected = frozenset(
        {
            "preview_version",
            "group_id",
            "source_group_digest",
            "speaker_scope_digest",
            "quality_basis",
            "quality_digest",
            "resolution_digest",
            "original_candidates",
            "resolution_state",
            "selected_candidate_digest",
            "unresolved_region_digests",
            "comparison_digest",
            "revision",
            "size_bytes",
        }
    )
    for raw in value:
        row = _mapping(raw, "speech_evidence_offer_preview_invalid")
        _closed(row, expected)
        if row.get("preview_version") != GROUP_PREVIEW_VERSION:
            raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_version_invalid")
        group_id = _identifier(row.get("group_id"), "speech_evidence_group_id_invalid")
        source_digest = _digest(
            row.get("source_group_digest"), "speech_evidence_source_group_digest_invalid"
        )
        for name in (
            "speaker_scope_digest",
            "quality_digest",
            "resolution_digest",
        ):
            _digest(row.get(name), f"speech_evidence_{name}_invalid")
        if row.get("quality_basis") not in {"decision", "policy"}:
            raise SpeechEvidenceProtocolError("speech_evidence_quality_basis_invalid")
        revision = _integer(
            row.get("revision"), 1, 2**31 - 1, "speech_evidence_preview_revision_invalid"
        )
        _integer(row.get("size_bytes"), 1, MAX_TOTAL_BYTES, "speech_evidence_preview_size_invalid")
        raw_candidates = row.get("original_candidates")
        if not isinstance(raw_candidates, list) or not 1 <= len(raw_candidates) <= MAX_CANDIDATES:
            raise SpeechEvidenceProtocolError("speech_evidence_candidate_projection_invalid")
        candidates: list[dict[str, Any]] = []
        candidate_digests: set[str] = set()
        for index, raw_candidate in enumerate(raw_candidates):
            candidate = _mapping(raw_candidate, "speech_evidence_candidate_projection_invalid")
            _closed(
                candidate,
                frozenset({"ordinal", "candidate_digest", "authority_digest", "revision"}),
            )
            ordinal = _integer(
                candidate.get("ordinal"), 1, MAX_CANDIDATES, "speech_evidence_candidate_projection_invalid"
            )
            if ordinal != index + 1:
                raise SpeechEvidenceProtocolError("speech_evidence_candidate_projection_invalid")
            candidate_digest = _digest(
                candidate.get("candidate_digest"), "speech_evidence_candidate_projection_invalid"
            )
            _digest(candidate.get("authority_digest"), "speech_evidence_candidate_projection_invalid")
            candidate_revision = _integer(
                candidate.get("revision"), 1, 2**31 - 1, "speech_evidence_candidate_projection_invalid"
            )
            if candidate_revision > revision:
                raise SpeechEvidenceProtocolError("speech_evidence_candidate_projection_invalid")
            if candidate_digest in candidate_digests:
                raise SpeechEvidenceProtocolError("speech_evidence_candidate_projection_invalid")
            candidate_digests.add(candidate_digest)
            candidates.append(dict(candidate))
        state = row.get("resolution_state")
        if state not in {"resolved", "unresolved"}:
            raise SpeechEvidenceProtocolError("speech_evidence_comparison_resolution_invalid")
        selected = row.get("selected_candidate_digest")
        if selected is not None:
            selected = _digest(selected, "speech_evidence_selected_candidate_digest_invalid")
        raw_regions = row.get("unresolved_region_digests")
        if not isinstance(raw_regions, list) or len(raw_regions) > 1024:
            raise SpeechEvidenceProtocolError("speech_evidence_unresolved_regions_invalid")
        regions = [
            _digest(item, "speech_evidence_unresolved_regions_invalid") for item in raw_regions
        ]
        if regions != sorted(set(regions)):
            raise SpeechEvidenceProtocolError("speech_evidence_unresolved_regions_invalid")
        if (
            (state == "resolved" and (selected not in candidate_digests or regions))
            or (state == "unresolved" and (selected is not None or not regions))
        ):
            raise SpeechEvidenceProtocolError("speech_evidence_comparison_resolution_invalid")
        comparison_digest = _digest(
            row.get("comparison_digest"), "speech_evidence_comparison_digest_invalid"
        )
        if comparison_digest != group_preview_comparison_digest(
            source_group_digest=source_digest,
            revision=revision,
            original_candidates=candidates,
            resolution_state=str(state),
            selected_candidate_digest=selected,
            unresolved_region_digests=regions,
        ):
            raise SpeechEvidenceProtocolError("speech_evidence_comparison_digest_mismatch")
        if group_id != group_preview_group_id(source_digest, revision):
            raise SpeechEvidenceProtocolError("speech_evidence_source_group_mismatch")
        if row.get("resolution_digest") != group_preview_resolution_digest(source_digest, revision):
            raise SpeechEvidenceProtocolError("speech_evidence_resolution_digest_mismatch")
        if group_id in group_ids or source_digest in source_digests:
            raise SpeechEvidenceProtocolError("speech_evidence_offer_preview_duplicate")
        group_ids.add(group_id)
        source_digests.add(source_digest)
        previews.append(dict(row))
    return tuple(previews)


def group_preview_group_id(source_group_digest: str, revision: int) -> str:
    """Derive a content-free group identifier from source lineage and revision."""

    _digest(source_group_digest, "speech_evidence_source_group_digest_invalid")
    _integer(revision, 1, 2**31 - 1, "speech_evidence_preview_revision_invalid")
    digest = canonical_sha256(
        {
            "domain": "ananta.speech-evidence-source-group.v1",
            "revision": revision,
            "source_group_digest": source_group_digest,
        }
    )
    return f"speech-group-{digest[:40]}"


def group_preview_resolution_digest(source_group_digest: str, revision: int) -> str:
    """Bind the advertised resolution scope without exposing resolution content."""

    _digest(source_group_digest, "speech_evidence_source_group_digest_invalid")
    _integer(revision, 1, 2**31 - 1, "speech_evidence_preview_revision_invalid")
    return canonical_sha256(
        {
            "domain": "ananta.speech-evidence-resolution-scope.v1",
            "revision": revision,
            "source_group_digest": source_group_digest,
        }
    )


def group_preview_comparison_digest(
    *,
    source_group_digest: str,
    revision: int,
    original_candidates: list[Mapping[str, Any]],
    resolution_state: str,
    selected_candidate_digest: str | None,
    unresolved_region_digests: list[str],
) -> str:
    """Bind the content-free candidate/resolution projection carried by an offer."""

    return canonical_sha256(
        {
            "domain": "ananta.speech-evidence-comparison-preview.v1",
            "source_group_digest": source_group_digest,
            "revision": revision,
            "original_candidates": [dict(value) for value in original_candidates],
            "resolution_state": resolution_state,
            "selected_candidate_digest": selected_candidate_digest,
            "unresolved_region_digests": list(unresolved_region_digests),
        }
    )


def _chunk(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "offer_id",
                "group_id",
                "chunk_index",
                "chunk_count",
                "plaintext_bytes",
                "plaintext_digest",
                "ciphertext_digest",
                "nonce_b64",
                "ciphertext_b64",
            }
        ),
    )
    if payload.get("traffic_class") != "evidence_bulk":
        raise SpeechEvidenceProtocolError("speech_evidence_traffic_class_invalid")
    _identifier(payload.get("offer_id"), "speech_evidence_offer_id_invalid")
    _identifier(payload.get("group_id"), "speech_evidence_group_id_invalid")
    count = _integer(payload.get("chunk_count"), 1, MAX_GROUPS, "speech_evidence_chunk_count_invalid")
    index = _integer(payload.get("chunk_index"), 0, count - 1, "speech_evidence_chunk_index_invalid")
    del index
    plain_size = _integer(
        payload.get("plaintext_bytes"), 1, MAX_CHUNK_PLAINTEXT_BYTES, "speech_evidence_chunk_oversized"
    )
    for name in ("plaintext_digest", "ciphertext_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    _b64(payload.get("nonce_b64"), "speech_evidence_nonce_invalid", exact_bytes=12)
    ciphertext = _b64(payload.get("ciphertext_b64"), "speech_evidence_ciphertext_invalid")
    if not 17 <= len(ciphertext) <= MAX_CHUNK_CIPHERTEXT_BYTES or len(ciphertext) != plain_size + 16:
        raise SpeechEvidenceProtocolError("speech_evidence_chunk_oversized")
    if hashlib.sha256(ciphertext).hexdigest() != payload.get("ciphertext_digest"):
        raise SpeechEvidenceProtocolError("speech_evidence_ciphertext_digest_mismatch")


def _chunk_ack(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "offer_id",
                "group_id",
                "acknowledged_indices",
                "first_missing_index",
                "received_bytes",
                "complete",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("offer_id"), "speech_evidence_offer_id_invalid")
    _identifier(payload.get("group_id"), "speech_evidence_group_id_invalid")
    indices = _integers(payload.get("acknowledged_indices"), MAX_GROUPS, 0, MAX_GROUPS - 1)
    if len(indices) != len(set(indices)) or indices != sorted(indices):
        raise SpeechEvidenceProtocolError("speech_evidence_ack_inconsistent")
    first_missing = _integer(
        payload.get("first_missing_index"), 0, MAX_GROUPS, "speech_evidence_ack_cursor_invalid"
    )
    if any(index >= first_missing for index in indices[:first_missing]) or set(range(first_missing)) - set(indices):
        raise SpeechEvidenceProtocolError("speech_evidence_ack_cursor_invalid")
    _integer(payload.get("received_bytes"), 0, MAX_TOTAL_BYTES, "speech_evidence_total_bytes_invalid")
    if payload.get("complete") not in {True, False}:
        raise SpeechEvidenceProtocolError("speech_evidence_ack_inconsistent")


def _resolution(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "resolution_id",
                "policy_version",
                "graph_digest",
                "candidate_ids",
                "accepted_candidate_ids",
                "unresolved_region_ids",
                "result_digest",
                "candidates",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("resolution_id"), "speech_evidence_resolution_id_invalid")
    _identifier(payload.get("policy_version"), "speech_evidence_policy_version_invalid")
    candidate_ids = _identifiers(payload.get("candidate_ids"), MAX_CANDIDATES, "speech_evidence_candidates_invalid")
    accepted = _identifiers(
        payload.get("accepted_candidate_ids"), MAX_CANDIDATES, "speech_evidence_candidates_invalid"
    )
    if not set(accepted) <= set(candidate_ids):
        raise SpeechEvidenceProtocolError("speech_evidence_resolution_inconsistent")
    _identifiers(payload.get("unresolved_region_ids"), 1024, "speech_evidence_regions_invalid")
    for name in ("graph_digest", "result_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) > MAX_CANDIDATES:
        raise SpeechEvidenceProtocolError("speech_evidence_candidates_invalid")
    parsed_ids: list[str] = []
    for raw in candidates:
        item = _mapping(raw, "speech_evidence_candidate_invalid")
        _closed(
            item,
            frozenset(
                {
                    "candidate_id",
                    "source_id",
                    "contributor_digest",
                    "revision",
                    "lineage_digest",
                    "text",
                    "confidence",
                    "start_ms",
                    "end_ms",
                }
            ),
        )
        parsed_ids.append(_identifier(item.get("candidate_id"), "speech_evidence_candidate_invalid"))
        _identifier(item.get("source_id"), "speech_evidence_source_invalid")
        _digest(item.get("contributor_digest"), "speech_evidence_contributor_invalid")
        _digest(item.get("lineage_digest"), "speech_evidence_lineage_invalid")
        _integer(item.get("revision"), 1, 2**31 - 1, "speech_evidence_revision_invalid")
        text = item.get("text")
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT_CHARS:
            raise SpeechEvidenceProtocolError("speech_evidence_text_invalid")
        _finite(item.get("confidence"), 0.0, 1.0, "speech_evidence_confidence_invalid")
        start = _integer(item.get("start_ms"), 0, MAX_SEQUENCE, "speech_evidence_timing_invalid")
        end = _integer(item.get("end_ms"), 0, MAX_SEQUENCE, "speech_evidence_timing_invalid")
        if end < start:
            raise SpeechEvidenceProtocolError("speech_evidence_timing_invalid")
    if set(parsed_ids) != set(candidate_ids) or len(parsed_ids) != len(set(parsed_ids)):
        raise SpeechEvidenceProtocolError("speech_evidence_resolution_inconsistent")


def _receipt(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "receipt_id",
                "offer_id",
                "inventory_root_digest",
                "resolution_digest",
                "accepted_group_ids",
                "rejected_group_ids",
                "quarantined_group_ids",
                "consent_digest",
                "policy_digest",
                "result_digest",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("receipt_id"), "speech_evidence_receipt_id_invalid")
    _identifier(payload.get("offer_id"), "speech_evidence_offer_id_invalid")
    groups = []
    for name in ("accepted_group_ids", "rejected_group_ids", "quarantined_group_ids"):
        values = _identifiers(payload.get(name), MAX_GROUPS, "speech_evidence_groups_invalid")
        groups.extend(values)
    if len(groups) != len(set(groups)):
        raise SpeechEvidenceProtocolError("speech_evidence_receipt_inconsistent")
    for name in (
        "inventory_root_digest",
        "resolution_digest",
        "consent_digest",
        "policy_digest",
        "result_digest",
    ):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")


def _revocation(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "revocation_id",
                "group_ids",
                "scope_digest",
                "reason_code",
                "revocation_epoch",
                "deadline_at_ms",
                "requested_action",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("revocation_id"), "speech_evidence_revocation_id_invalid")
    _identifiers(payload.get("group_ids"), MAX_GROUPS, "speech_evidence_groups_invalid")
    _digest(payload.get("scope_digest"), "speech_evidence_scope_digest_invalid")
    _identifier(payload.get("reason_code"), "speech_evidence_reason_invalid")
    _integer(payload.get("revocation_epoch"), 1, 2**31 - 1, "speech_evidence_revocation_epoch_invalid")
    _integer(payload.get("deadline_at_ms"), 1, MAX_SEQUENCE, "speech_evidence_deadline_invalid")
    if payload.get("requested_action") not in {"delete", "stop_use"}:
        raise SpeechEvidenceProtocolError("speech_evidence_revocation_action_invalid")


def _revocation_ack(payload: Mapping[str, Any]) -> None:
    _closed(
        payload,
        frozenset(
            {
                "traffic_class",
                "revocation_id",
                "scope_digest",
                "revocation_epoch",
                "impact_digest",
                "group_results",
                "decision",
            }
        ),
    )
    _control(payload)
    _identifier(payload.get("revocation_id"), "speech_evidence_revocation_id_invalid")
    for name in ("scope_digest", "impact_digest"):
        _digest(payload.get(name), f"speech_evidence_{name}_invalid")
    _integer(payload.get("revocation_epoch"), 1, 2**31 - 1, "speech_evidence_revocation_epoch_invalid")
    results = payload.get("group_results")
    if not isinstance(results, list) or not results or len(results) > MAX_GROUPS:
        raise SpeechEvidenceProtocolError("speech_evidence_revocation_ack_invalid")
    seen: set[str] = set()
    for raw in results:
        item = _mapping(raw, "speech_evidence_revocation_ack_invalid")
        _closed(item, frozenset({"group_id", "state", "reason_code"}))
        group_id = _identifier(item.get("group_id"), "speech_evidence_group_id_invalid")
        if group_id in seen or item.get("state") not in {"deleted", "use_stopped", "not_found", "unresolved"}:
            raise SpeechEvidenceProtocolError("speech_evidence_revocation_ack_invalid")
        seen.add(group_id)
        _identifier(item.get("reason_code"), "speech_evidence_reason_invalid")
    if payload.get("decision") not in {"complete", "partial", "unresolved"}:
        raise SpeechEvidenceProtocolError("speech_evidence_revocation_ack_invalid")


_PAYLOAD_VALIDATORS = {
    "inventory": _inventory,
    "diff": _diff,
    "offer": _offer,
    "chunk": _chunk,
    "chunk_ack": _chunk_ack,
    "resolution": _resolution,
    "receipt": _receipt,
    "revocation": _revocation,
    "revocation_ack": _revocation_ack,
}


__all__ = [
    "group_preview_comparison_digest",
    "group_preview_group_id",
    "group_preview_resolution_digest",
    "validate_payload",
]
