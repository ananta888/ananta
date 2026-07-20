"""Strict, content-free contracts for Hub-owned speech evidence governance.

The contract deliberately does not carry transcript or audio payloads.  It is
safe to embed in Hub tasks and audit records and is shared by API, persistence
and worker-result admission code.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

SPEECH_EVIDENCE_CONSENT_SCHEMA = "ananta.speech-evidence-consent.v1"
SPEECH_EVIDENCE_CURATION_TASK_SCHEMA = "ananta.speech-evidence-curation-task.v1"
SPEECH_EVIDENCE_CURATION_RESULT_SCHEMA = "ananta.speech-evidence-curation-result.v1"

SPEECH_GRANTS = (
    "capture",
    "transcript_share",
    "feature_share",
    "raw_audio_share",
    "dataset_import",
    "training",
    "inference",
    "export",
)
SPEECH_DIRECTIONS = frozenset({"local", "sender_to_receiver", "receiver_to_sender"})
SPEECH_DATA_CLASSES = frozenset(
    {
        "audio",
        "transcript",
        "acoustic_features",
        "speaker_embedding",
        "correction",
        "quality_metrics",
    }
)
SPEECH_CONSENT_STATES = frozenset({"active", "revoked", "expired"})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,159}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_CONSENT_LIFETIME_MS = 366 * 24 * 60 * 60 * 1000
_MAX_CLOCK_SKEW_MS = 5 * 60 * 1000


class SpeechEvidenceGovernanceError(ValueError):
    def __init__(self, reason_code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.status_code = status_code


def canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpeechEvidenceGovernanceError(
            "speech_governance_json_invalid", "governance payload is not canonical JSON"
        ) from exc


def sha256_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class SpeechEvidenceConsent:
    consent_id: str
    tenant_id: str
    owner_subject: str
    speaker_id: str
    recipient_id: str
    direction: str
    pair_id: str
    session_id: str
    session_epoch: int
    purpose: str
    data_classes: tuple[str, ...]
    retention_seconds: int
    trainer_locations: tuple[str, ...]
    grant_items: tuple[tuple[str, bool], ...]
    consent_version: int
    revocation_epoch: int
    issued_at_ms: int
    expires_at_ms: int
    state: str = "active"
    required_signers: tuple[str, ...] = ()
    signature_items: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_mapping(cls, raw: object, *, now_ms: int | None = None) -> "SpeechEvidenceConsent":
        data = _mapping(raw, "consent")
        allowed = {
            "schema",
            "consent_id",
            "tenant_id",
            "owner_subject",
            "speaker_id",
            "recipient_id",
            "direction",
            "pair_id",
            "session_id",
            "session_epoch",
            "purpose",
            "data_classes",
            "retention_seconds",
            "trainer_locations",
            "grants",
            "consent_version",
            "revocation_epoch",
            "issued_at_ms",
            "expires_at_ms",
            "state",
            "required_signers",
            "signatures",
        }
        _closed(data, allowed, "consent")
        if data.get("schema") != SPEECH_EVIDENCE_CONSENT_SCHEMA:
            raise SpeechEvidenceGovernanceError("speech_consent_schema_invalid", "unsupported speech consent schema")
        direction = _enum(data.get("direction"), SPEECH_DIRECTIONS, "direction")
        issued_at_ms = _integer(data.get("issued_at_ms"), "issued_at_ms", minimum=0)
        expires_at_ms = _integer(data.get("expires_at_ms"), "expires_at_ms", minimum=1)
        if expires_at_ms <= issued_at_ms:
            raise SpeechEvidenceGovernanceError("speech_consent_expiry_invalid", "consent expiry must follow issuance")
        if expires_at_ms - issued_at_ms > _MAX_CONSENT_LIFETIME_MS:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_expiry_invalid", "speech consent lifetime exceeds its bound"
            )
        if now_ms is not None and issued_at_ms > now_ms + _MAX_CLOCK_SKEW_MS:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_issued_at_invalid", "speech consent issuance is in the future"
            )
        if now_ms is not None and expires_at_ms <= now_ms:
            raise SpeechEvidenceGovernanceError("speech_consent_expired", "speech consent is expired", status_code=403)
        grants = normalize_grants(data.get("grants"))
        data_classes = _string_set(data.get("data_classes"), "data_classes", SPEECH_DATA_CLASSES, maximum=16)
        trainers = _identifiers(data.get("trainer_locations"), "trainer_locations", maximum=32)
        if grants["training"] and not trainers:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_trainer_location_required",
                "training grants must bind at least one trainer location",
            )
        if grants["raw_audio_share"] and "audio" not in data_classes:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_raw_audio_class_missing",
                "raw-audio sharing requires the audio data class",
            )
        required_signers = _identifiers(data.get("required_signers", []), "required_signers", maximum=4)
        signatures_raw = _mapping(data.get("signatures", {}), "signatures")
        signatures: list[tuple[str, str]] = []
        for signer, signature in signatures_raw.items():
            signer_id = _identifier(signer, "signature signer")
            signatures.append((signer_id, _digest(signature, f"signatures.{signer_id}")))
        if set(required_signers) != {name for name, _ in signatures}:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_signatures_incomplete",
                "all and only required contributor signatures must be bound",
                status_code=403,
            )
        speaker = _identifier(data.get("speaker_id"), "speaker_id")
        recipient = _identifier(data.get("recipient_id"), "recipient_id")
        if direction != "local" and speaker == recipient:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_recipient_invalid", "shared speech consent needs a distinct recipient"
            )
        if direction != "local" and set(required_signers) != {speaker, recipient}:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_bilateral_signers_required",
                "shared speech evidence must bind both speaker and recipient signatures",
                status_code=403,
            )
        return cls(
            consent_id=_identifier(data.get("consent_id"), "consent_id"),
            tenant_id=_identifier(data.get("tenant_id"), "tenant_id"),
            owner_subject=_identifier(data.get("owner_subject"), "owner_subject"),
            speaker_id=speaker,
            recipient_id=recipient,
            direction=direction,
            pair_id=_identifier(data.get("pair_id"), "pair_id"),
            session_id=_identifier(data.get("session_id"), "session_id"),
            session_epoch=_integer(data.get("session_epoch"), "session_epoch", minimum=1),
            purpose=_identifier(data.get("purpose"), "purpose"),
            data_classes=data_classes,
            retention_seconds=_integer(
                data.get("retention_seconds"), "retention_seconds", minimum=60, maximum=31_536_000
            ),
            trainer_locations=trainers,
            grant_items=tuple((grant, grants[grant]) for grant in SPEECH_GRANTS),
            consent_version=_integer(data.get("consent_version"), "consent_version", minimum=1),
            revocation_epoch=_integer(data.get("revocation_epoch"), "revocation_epoch", minimum=0),
            issued_at_ms=issued_at_ms,
            expires_at_ms=expires_at_ms,
            state=_enum(data.get("state", "active"), SPEECH_CONSENT_STATES, "state"),
            required_signers=required_signers,
            signature_items=tuple(sorted(signatures)),
        )

    @property
    def grants(self) -> dict[str, bool]:
        return dict(self.grant_items)

    @property
    def signatures(self) -> dict[str, str]:
        return dict(self.signature_items)

    @property
    def scope_digest(self) -> str:
        return sha256_digest(self.scope_mapping())

    @property
    def consent_digest(self) -> str:
        return sha256_digest(self.to_dict())

    def scope_mapping(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "owner_subject": self.owner_subject,
            "speaker_id": self.speaker_id,
            "recipient_id": self.recipient_id,
            "direction": self.direction,
            "pair_id": self.pair_id,
            "session_id": self.session_id,
            "session_epoch": self.session_epoch,
            "purpose": self.purpose,
            "data_classes": list(self.data_classes),
            "retention_seconds": self.retention_seconds,
            "trainer_locations": list(self.trainer_locations),
            "grants": self.grants,
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SPEECH_EVIDENCE_CONSENT_SCHEMA,
            "consent_id": self.consent_id,
            **self.scope_mapping(),
            "consent_version": self.consent_version,
            "revocation_epoch": self.revocation_epoch,
            "issued_at_ms": self.issued_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "state": self.state,
            "required_signers": list(self.required_signers),
            "signatures": self.signatures,
        }

    def allows(
        self,
        grant: str,
        *,
        tenant_id: str,
        owner_subject: str,
        speaker_id: str,
        recipient_id: str,
        direction: str,
        pair_id: str,
        session_id: str,
        session_epoch: int,
        purpose: str,
        data_class: str,
        trainer_location: str | None = None,
        now_ms: int,
    ) -> None:
        if grant not in SPEECH_GRANTS or not self.grants.get(grant, False):
            raise SpeechEvidenceGovernanceError(
                "speech_consent_grant_missing", f"speech grant {grant!r} is not active", status_code=403
            )
        if self.state != "active" or now_ms >= self.expires_at_ms:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_inactive", "speech consent is not active", status_code=403
            )
        expected = (
            self.tenant_id,
            self.owner_subject,
            self.speaker_id,
            self.recipient_id,
            self.direction,
            self.pair_id,
            self.session_id,
            self.session_epoch,
            self.purpose,
        )
        actual = (
            tenant_id,
            owner_subject,
            speaker_id,
            recipient_id,
            direction,
            pair_id,
            session_id,
            session_epoch,
            purpose,
        )
        if actual != expected:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_scope_mismatch",
                "claim does not match tenant, owner, participant, direction, session, epoch or purpose",
                status_code=403,
            )
        if data_class not in self.data_classes:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_data_class_missing", "data class is not consented", status_code=403
            )
        if grant == "training" and trainer_location not in self.trainer_locations:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_trainer_location_mismatch",
                "trainer location is not consented",
                status_code=403,
            )

    def is_scope_subset_of(self, other: "SpeechEvidenceConsent") -> bool:
        immutable = (
            "tenant_id",
            "owner_subject",
            "speaker_id",
            "recipient_id",
            "direction",
            "pair_id",
            "session_id",
            "session_epoch",
            "purpose",
        )
        if any(getattr(self, name) != getattr(other, name) for name in immutable):
            return False
        return (
            set(self.data_classes) <= set(other.data_classes)
            and set(self.trainer_locations) <= set(other.trainer_locations)
            and self.retention_seconds <= other.retention_seconds
            and all(not value or other.grants[name] for name, value in self.grant_items)
            and set(self.required_signers) == set(other.required_signers)
        )


def normalize_grants(raw: object | None) -> dict[str, bool]:
    """Return every grant explicitly, default-deny, and reject aliases/unknowns."""

    if raw is None:
        return {name: False for name in SPEECH_GRANTS}
    data = _mapping(raw, "grants")
    unknown = sorted(set(data) - set(SPEECH_GRANTS))
    if unknown:
        raise SpeechEvidenceGovernanceError(
            "speech_consent_grant_unknown", f"unknown speech grants: {', '.join(unknown)}"
        )
    result = {name: False for name in SPEECH_GRANTS}
    for name, value in data.items():
        if type(value) is not bool:
            raise SpeechEvidenceGovernanceError(
                "speech_consent_grant_invalid", f"grant {name} must be an exact boolean"
            )
        result[name] = value
    return result


def migrate_legacy_categories(categories: object) -> dict[str, bool]:
    """Conservatively map legacy personalization categories.

    Legacy categories never grant sharing, dataset, training, raw-audio or
    export rights.  They may only retain local inference for explicit
    personalization categories.
    """

    if not isinstance(categories, list) or any(not isinstance(item, str) for item in categories):
        raise SpeechEvidenceGovernanceError(
            "speech_consent_legacy_categories_invalid", "legacy categories must be a string list"
        )
    result = {name: False for name in SPEECH_GRANTS}
    known_local = {"preferences", "text_corrections", "vocabulary", "audio_fingerprint"}
    if set(categories) <= known_local and categories:
        result["inference"] = True
    return result


@dataclass(frozen=True)
class SpeechCurationWorkerTask:
    task_id: str
    parent_task_id: str
    admission_digest: str
    evidence_refs: tuple[str, ...]
    consent_id: str
    consent_version: int
    revocation_epoch: int
    deadline_epoch_ms: int
    limits: Mapping[str, int]
    artifact_publish_ref: str
    fencing_token: int

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": SPEECH_EVIDENCE_CURATION_TASK_SCHEMA,
            "task_id": self.task_id,
            "parent_task_id": self.parent_task_id,
            "admission_digest": self.admission_digest,
            "evidence_refs": list(self.evidence_refs),
            "consent_id": self.consent_id,
            "consent_version": self.consent_version,
            "revocation_epoch": self.revocation_epoch,
            "deadline_epoch_ms": self.deadline_epoch_ms,
            "limits": dict(self.limits),
            "artifact_publish_ref": self.artifact_publish_ref,
            "fencing_token": self.fencing_token,
        }


@dataclass(frozen=True)
class SpeechCurationWorkerResult:
    task_id: str
    admission_digest: str
    artifact_ref: str
    artifact_digest: str
    consent_version: int
    revocation_epoch: int
    fencing_token: int
    completed_at_ms: int

    @classmethod
    def from_mapping(cls, raw: object) -> "SpeechCurationWorkerResult":
        data = _mapping(raw, "curation_result")
        allowed = {
            "schema",
            "task_id",
            "admission_digest",
            "artifact_ref",
            "artifact_digest",
            "consent_version",
            "revocation_epoch",
            "fencing_token",
            "completed_at_ms",
        }
        _closed(data, allowed, "curation_result")
        if data.get("schema") != SPEECH_EVIDENCE_CURATION_RESULT_SCHEMA:
            raise SpeechEvidenceGovernanceError(
                "speech_curation_result_schema_invalid", "unsupported curation result schema"
            )
        artifact_ref = str(data.get("artifact_ref") or "")
        if not artifact_ref.startswith("artifact://speech-curation/") or ".." in artifact_ref.split("/"):
            raise SpeechEvidenceGovernanceError(
                "speech_curation_artifact_ref_invalid", "curation result artifact reference is invalid"
            )
        return cls(
            task_id=_identifier(data.get("task_id"), "task_id"),
            admission_digest=_digest(data.get("admission_digest"), "admission_digest"),
            artifact_ref=artifact_ref,
            artifact_digest=_digest(data.get("artifact_digest"), "artifact_digest"),
            consent_version=_integer(data.get("consent_version"), "consent_version", minimum=1),
            revocation_epoch=_integer(data.get("revocation_epoch"), "revocation_epoch", minimum=0),
            fencing_token=_integer(data.get("fencing_token"), "fencing_token", minimum=1),
            completed_at_ms=_integer(data.get("completed_at_ms"), "completed_at_ms", minimum=1),
        )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise SpeechEvidenceGovernanceError("speech_governance_mapping_invalid", f"{field} must be an object")
    return dict(value)


def _closed(data: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise SpeechEvidenceGovernanceError(
            "speech_governance_unknown_field", f"{field} contains unknown fields: {', '.join(unknown)}"
        )


def _identifier(value: object, field: str) -> str:
    text = value if isinstance(value, str) else ""
    if _IDENTIFIER.fullmatch(text) is None:
        raise SpeechEvidenceGovernanceError("speech_governance_identifier_invalid", f"{field} is not a safe identifier")
    return text


def _digest(value: object, field: str) -> str:
    text = value if isinstance(value, str) else ""
    if _DIGEST.fullmatch(text) is None:
        raise SpeechEvidenceGovernanceError(
            "speech_governance_digest_invalid", f"{field} must be a lowercase SHA-256 digest"
        )
    return text


def _integer(value: object, field: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise SpeechEvidenceGovernanceError(
            "speech_governance_integer_invalid", f"{field} is outside its allowed range"
        )
    if maximum is not None and value > maximum:
        raise SpeechEvidenceGovernanceError(
            "speech_governance_integer_invalid", f"{field} is outside its allowed range"
        )
    return value


def _enum(value: object, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise SpeechEvidenceGovernanceError("speech_governance_enum_invalid", f"{field} has an unsupported value")
    return value


def _identifiers(value: object, field: str, *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise SpeechEvidenceGovernanceError("speech_governance_list_invalid", f"{field} must be a bounded list")
    normalized = tuple(sorted({_identifier(item, field) for item in value}))
    if len(normalized) != len(value):
        raise SpeechEvidenceGovernanceError("speech_governance_list_duplicate", f"{field} contains duplicates")
    return normalized


def _string_set(value: object, field: str, allowed: frozenset[str], *, maximum: int) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > maximum:
        raise SpeechEvidenceGovernanceError(
            "speech_governance_list_invalid", f"{field} must be a non-empty bounded list"
        )
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise SpeechEvidenceGovernanceError(
            "speech_governance_data_class_invalid", f"{field} contains an unsupported value"
        )
    normalized = tuple(sorted(set(value)))
    if len(normalized) != len(value):
        raise SpeechEvidenceGovernanceError("speech_governance_list_duplicate", f"{field} contains duplicates")
    return normalized


__all__ = [
    "SPEECH_DATA_CLASSES",
    "SPEECH_DIRECTIONS",
    "SPEECH_EVIDENCE_CONSENT_SCHEMA",
    "SPEECH_GRANTS",
    "SpeechCurationWorkerResult",
    "SpeechCurationWorkerTask",
    "SpeechEvidenceConsent",
    "SpeechEvidenceGovernanceError",
    "canonical_json",
    "migrate_legacy_categories",
    "normalize_grants",
    "sha256_digest",
]
