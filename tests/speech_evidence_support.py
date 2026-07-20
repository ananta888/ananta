from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from agent.services.voice_governance_domain import VoicePrincipal
from ananta_contracts.speech_evidence_governance import SPEECH_GRANTS
from voice_runtime.evidence_identity import SpeechEvidenceIdentityService


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def consent_payload(
    prefix: str,
    *,
    grants: dict[str, bool] | None = None,
    now_ms: int | None = None,
    expires_in_ms: int = 3_600_000,
) -> dict[str, object]:
    now = int(now_ms if now_ms is not None else time.time_ns() // 1_000_000)
    effective = {name: False for name in SPEECH_GRANTS}
    effective.update(
        grants
        or {
            "capture": True,
            "transcript_share": True,
            "feature_share": True,
            "raw_audio_share": True,
        }
    )
    return {
        "schema": "ananta.speech-evidence-consent.v1",
        "consent_id": f"consent-{prefix}",
        "tenant_id": f"tenant-{prefix}",
        "owner_subject": f"owner-{prefix}",
        "speaker_id": f"speaker-{prefix}",
        "recipient_id": f"recipient-{prefix}",
        "direction": "sender_to_receiver",
        "pair_id": f"pair-{prefix}",
        "session_id": f"session-{prefix}",
        "session_epoch": 1,
        "purpose": "speech_quality_improvement",
        "data_classes": ["audio", "transcript", "acoustic_features", "correction"],
        "retention_seconds": 3600,
        "trainer_locations": ["trainer-local"] if effective["training"] else [],
        "grants": effective,
        "consent_version": 1,
        "revocation_epoch": 0,
        "issued_at_ms": now,
        "expires_at_ms": now + expires_in_ms,
        "state": "active",
        "required_signers": [f"recipient-{prefix}", f"speaker-{prefix}"],
        "signatures": {
            f"recipient-{prefix}": digest(f"recipient-signature-{prefix}"),
            f"speaker-{prefix}": digest(f"speaker-signature-{prefix}"),
        },
    }


def principal(prefix: str) -> VoicePrincipal:
    return VoicePrincipal(f"tenant-{prefix}", f"owner-{prefix}")


def identity(prefix: str, payload: bytes):
    return SpeechEvidenceIdentityService(b"i" * 32).identify(
        pair_id=f"pair-{prefix}",
        session_id=f"session-{prefix}",
        session_epoch=1,
        speaker_scope=f"speaker-{prefix}",
        capture_segment_id=f"segment-{prefix}",
        start_ms=0,
        end_ms=1000,
        source_digest=hashlib.sha256(payload).hexdigest(),
        revision=1,
        revision_digest=hashlib.sha256(b"revision:" + payload).hexdigest(),
    )


def stored_evidence(
    prefix: str,
    payload: bytes,
    *,
    evidence_class: str = "transcript",
    audit=None,
):
    from agent.services.speech_evidence_consent_service import SpeechEvidenceConsentService
    from agent.services.speech_evidence_store_service import SpeechEvidenceStoreService

    consent_service = SpeechEvidenceConsentService()
    consent = consent_service.grant(principal(prefix), consent_payload(prefix))
    store = SpeechEvidenceStoreService(
        consent=consent_service,
        digest_key=b"d" * 32,
        audit=audit,
    )
    grant = {
        "transcript": "transcript_share",
        "correction": "transcript_share",
        "audio": "raw_audio_share",
        "acoustic_features": "feature_share",
    }.get(evidence_class, "feature_share")
    data_class = evidence_class if evidence_class != "correction" else "correction"
    record, created = store.store(
        principal(prefix),
        payload,
        claimed_content_digest=hashlib.sha256(payload).hexdigest(),
        provenance_digest=digest(f"provenance-{prefix}"),
        identity=identity(prefix, payload),
        evidence_class=evidence_class,
        data_class=data_class,
        grant=grant,
        consent_id=consent.consent_id,
        consent_version=consent.consent_version,
        revocation_epoch=consent.revocation_epoch,
        consent_digest=consent.consent_digest,
        speaker_id=consent.speaker_id,
        recipient_id=consent.recipient_id,
        direction=consent.direction,
        pair_id=consent.pair_id,
        session_id=consent.session_id,
        session_epoch=consent.session_epoch,
        purpose=consent.purpose,
        retention_seconds=600,
    )
    assert created
    return consent_service, store, consent, record


@dataclass
class AllowAuthority:
    calls: list[dict[str, object]] = field(default_factory=list)

    def authorize(self, **kwargs: object) -> tuple[bool, str]:
        self.calls.append(dict(kwargs))
        return True, "ok"


class AcceptPublisher:
    def publish_manifest(self, **_kwargs: object) -> bool:
        return True


class AllowDatasetConsent:
    def authorize_import(self, _principal, **_kwargs: object) -> None:
        return None

    def fence_imports(self, _session, _principal, **_kwargs: object) -> None:
        return None


class AcceptResultPublisher:
    def publish(self, _result) -> bool:
        return True


@dataclass
class QueueRecorder:
    calls: list[dict[str, object]] = field(default_factory=list)

    def ingest_task(self, **kwargs: object) -> None:
        self.calls.append(dict(kwargs))


def manifest_record(prefix: str, *, group_suffix: str = "a") -> dict[str, object]:
    contributor = digest(f"contributor-{prefix}")
    source = digest(f"source-{prefix}")
    return {
        "record_digest": digest(f"record-{prefix}"),
        "lineage_kind": "evidence",
        "source_digest": source,
        "utterance_family_id": f"utterance-v1:{digest(f'utterance-{prefix}')}",
        "session_group_id": digest(f"session-group-{group_suffix}"),
        "near_duplicate_group_id": digest(f"near-duplicate-{group_suffix}"),
        "contributors": [contributor],
        "data_classes": ["audio", "transcript"],
        "field_provenance": {
            "audio": {"contributor_digest": contributor, "source_digest": source},
            "transcript": {"contributor_digest": contributor, "source_digest": source},
        },
        "consent_refs": [
            {
                "consent_id": f"consent-{prefix}",
                "consent_version": 1,
                "revocation_epoch": 0,
                "consent_digest": digest(f"consent-digest-{prefix}"),
            }
        ],
        "duration_ms": 1000,
        "curation_report_digest": digest(f"curation-{prefix}"),
    }
