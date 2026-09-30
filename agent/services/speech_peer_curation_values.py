"""Closed value types and bounded parsers for peer speech-evidence curation.

Extracted from ``agent.services.speech_evidence_peer_curation_composition``
(SRP): request/record value objects, the curation error type and the pure,
fail-closed parsers/digest helpers carry no persistence or orchestration.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import (
    SpeechCurationWorkerResult,
    canonical_json,
)

DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
GROUP_SCHEMA = "ananta.peer-transcript-evidence.v1"
INPUT_SCHEMA = "ananta.peer-speech-hub-curation-input.v1"
ARTIFACT_SCHEMA = "ananta.peer-speech-curation-artifact.v1"
MAX_GROUP_BYTES = 1024 * 1024
MAX_AGGREGATE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 32_768


class SpeechPeerCurationError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 409) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class SpeechPeerCurationGroupInput:
    group_id: str
    chunks_b64: tuple[str, ...]

    @classmethod
    def from_mapping(cls, raw: object) -> "SpeechPeerCurationGroupInput":
        value = require_closed_mapping(raw, {"group_id", "chunks_b64"}, "speech_peer_curation_group_invalid")
        group_id = require_identifier(value.get("group_id"), "speech_peer_curation_group_invalid")
        chunks = value.get("chunks_b64")
        if not isinstance(chunks, list) or not chunks or len(chunks) > 16_384:
            raise SpeechPeerCurationError("speech_peer_curation_chunks_invalid", status_code=422)
        rendered = tuple(str(item) for item in chunks)
        if any(not item or len(item) > 96 * 1024 for item in rendered):
            raise SpeechPeerCurationError("speech_peer_curation_chunk_invalid", status_code=413)
        return cls(group_id=group_id, chunks_b64=rendered)


@dataclass(frozen=True)
class SpeechPeerCurationRecord:
    curation_id: str
    offer_id: str
    admission_digest: str
    state: str
    receipt: Mapping[str, object]
    curation_task_id: str | None
    dataset_id: str
    dataset_parent_digest: str | None
    dataset_manifest_digest: str | None
    consent_version: int
    revocation_epoch: int

    def public_dict(self) -> dict[str, object]:
        return {
            "curation_id": self.curation_id,
            "offer_id": self.offer_id,
            "admission_digest": self.admission_digest,
            "state": self.state,
            "receipt": dict(self.receipt),
            "curation_task_id": self.curation_task_id,
            "dataset_id": self.dataset_id,
            "dataset_parent_digest": self.dataset_parent_digest,
            "dataset_manifest_digest": self.dataset_manifest_digest,
            "consent_version": self.consent_version,
            "revocation_epoch": self.revocation_epoch,
        }


def parse_group(body: bytes) -> dict[str, object]:
    try:
        raw = json.loads(body.decode("utf-8"), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise SpeechPeerCurationError("speech_peer_curation_group_payload_invalid", status_code=422) from exc
    value = require_closed_mapping(
        raw,
        {"schema", "turn_id", "revision", "state", "source_digest", "candidates"},
        "speech_peer_curation_group_payload_invalid",
    )
    if value.get("schema") != GROUP_SCHEMA:
        raise SpeechPeerCurationError("speech_peer_curation_group_schema_invalid", status_code=422)
    turn_id = require_identifier(value.get("turn_id"), "speech_peer_curation_turn_invalid")
    revision = require_integer(value.get("revision"), 1, 2_147_483_647, "speech_peer_curation_revision_invalid")
    state = str(value.get("state") or "")
    if state not in {"final", "corrected", "correction_failed"}:
        raise SpeechPeerCurationError("speech_peer_curation_state_invalid", status_code=422)
    source_digest = require_digest(value.get("source_digest"), "speech_peer_curation_source_digest_invalid")
    raw_candidates = value.get("candidates")
    if not isinstance(raw_candidates, list) or not 1 <= len(raw_candidates) <= 32:
        raise SpeechPeerCurationError("speech_peer_curation_candidates_invalid", status_code=422)
    candidates: list[dict[str, object]] = []
    for raw_candidate in raw_candidates:
        candidate = require_closed_mapping(
            raw_candidate,
            {"revision", "authority", "text"},
            "speech_peer_curation_candidate_invalid",
        )
        text = candidate.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT_CHARS:
            raise SpeechPeerCurationError("speech_peer_curation_candidate_text_invalid", status_code=422)
        candidates.append(
            {
                "revision": require_integer(
                    candidate.get("revision"), 1, 2_147_483_647, "speech_peer_curation_candidate_invalid"
                ),
                "authority": require_identifier(
                    candidate.get("authority"), "speech_peer_curation_candidate_authority_invalid"
                ),
                "text": text,
            }
        )
    return {
        "schema": GROUP_SCHEMA,
        "turn_id": turn_id,
        "revision": revision,
        "state": state,
        "source_digest": source_digest,
        "candidates": candidates,
    }


def curation_artifact(raw: object, result: SpeechCurationWorkerResult) -> dict[str, object]:
    value = require_closed_mapping(
        raw,
        {
            "schema",
            "task_id",
            "admission_digest",
            "evidence_record_digest",
            "curation_report_digest",
            "resolution_policy_version",
            "duration_ms",
        },
        "speech_peer_curation_artifact_invalid",
    )
    artifact = {
        "schema": str(value.get("schema") or ""),
        "task_id": require_identifier(value.get("task_id"), "speech_peer_curation_artifact_task_invalid"),
        "admission_digest": require_digest(
            value.get("admission_digest"), "speech_peer_curation_artifact_admission_invalid"
        ),
        "evidence_record_digest": require_digest(
            value.get("evidence_record_digest"), "speech_peer_curation_artifact_evidence_invalid"
        ),
        "curation_report_digest": require_digest(
            value.get("curation_report_digest"), "speech_peer_curation_artifact_report_invalid"
        ),
        "resolution_policy_version": require_identifier(
            value.get("resolution_policy_version"), "speech_peer_curation_artifact_policy_invalid"
        ),
        "duration_ms": require_integer(
            value.get("duration_ms"), 1, 3_600_000, "speech_peer_curation_artifact_duration_invalid"
        ),
    }
    if (
        artifact["schema"] != ARTIFACT_SCHEMA
        or artifact["task_id"] != result.task_id
        or artifact["admission_digest"] != result.admission_digest
    ):
        raise SpeechPeerCurationError("speech_peer_curation_artifact_binding_mismatch", status_code=409)
    return artifact


def require_closed_mapping(raw: object, fields: set[str], reason_code: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or any(not isinstance(key, str) for key in raw) or set(raw) != fields:
        raise SpeechPeerCurationError(reason_code, status_code=422)
    return dict(raw)


def require_identifier(raw: object, reason_code: str) -> str:
    value = str(raw or "") if isinstance(raw, str) else ""
    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise SpeechPeerCurationError(reason_code, status_code=422)
    return value


def require_digest(raw: object, reason_code: str) -> str:
    value = str(raw or "") if isinstance(raw, str) else ""
    if DIGEST_PATTERN.fullmatch(value) is None:
        raise SpeechPeerCurationError(reason_code, status_code=422)
    return value


def require_integer(raw: object, minimum: int, maximum: int, reason_code: str) -> int:
    if type(raw) is not int or not minimum <= raw <= maximum:
        raise SpeechPeerCurationError(reason_code, status_code=422)
    return raw


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def peer_dataset_id(principal: VoicePrincipal, pair_id: str) -> str:
    value_digest = joined_digest(principal.tenant_id, principal.subject, pair_id)
    return f"speech-peer-dataset-{value_digest[:32]}"


def joined_digest(*values: str) -> str:
    return hashlib.sha256("\0".join(values).encode()).hexdigest()


__all__ = [
    "ARTIFACT_SCHEMA",
    "DIGEST_PATTERN",
    "GROUP_SCHEMA",
    "IDENTIFIER_PATTERN",
    "INPUT_SCHEMA",
    "MAX_AGGREGATE_BYTES",
    "MAX_GROUP_BYTES",
    "MAX_TEXT_CHARS",
    "SpeechPeerCurationError",
    "SpeechPeerCurationGroupInput",
    "SpeechPeerCurationRecord",
    "canonical_sha256",
    "curation_artifact",
    "joined_digest",
    "parse_group",
    "peer_dataset_id",
    "require_closed_mapping",
    "require_digest",
    "require_identifier",
    "require_integer",
]
