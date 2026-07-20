from __future__ import annotations

from types import SimpleNamespace

from agent.services.speech_evidence_revocation_service import (
    SpeechEvidenceRevocationError,
    SpeechEvidenceRevocationService,
)
from agent.services.voice_governance_domain import VoicePrincipal


class _ConsentWorklist:
    def __init__(self, evidence_ids: tuple[str, ...]) -> None:
        self._rows = tuple(SimpleNamespace(evidence_id=value) for value in evidence_ids)
        self.calls: list[dict[str, object]] = []

    def list_by_consent(self, **values):
        self.calls.append(dict(values))
        return self._rows[: int(values["limit"])]


def test_consent_revocation_cascade_is_bounded_and_reports_unresolved_work(monkeypatch) -> None:
    worklist = _ConsentWorklist(("evidence-a", "evidence-b", "evidence-c"))
    service = SpeechEvidenceRevocationService(evidence=worklist)  # type: ignore[arg-type]
    calls: list[tuple[str, int]] = []

    def revoke(_principal, evidence_id, *, expected_consent_version, **_values):
        calls.append((evidence_id, expected_consent_version))
        if evidence_id == "evidence-b":
            raise SpeechEvidenceRevocationError("speech_revocation_lineage_unavailable", status_code=503)
        return SimpleNamespace(idempotent_replay=False, unresolved=(), remote_state="not_requested")

    monkeypatch.setattr(service, "revoke", revoke)
    result = service.revoke_consent(
        VoicePrincipal("tenant-a", "owner-a"),
        "consent-a",
        expected_consent_version=4,
        limit=2,
    )

    assert calls == [("evidence-a", 4), ("evidence-b", 4)]
    assert result.public_dict() == {
        "consent_id": "consent-a",
        "scanned_count": 2,
        "revoked_count": 1,
        "replayed_count": 0,
        "unresolved_count": 2,
        "truncated": True,
        "reason_codes": [
            "speech_revocation_consent_worklist_truncated",
            "speech_revocation_lineage_unavailable",
        ],
    }
    assert worklist.calls == [
        {
            "tenant_id": "tenant-a",
            "owner_subject": "owner-a",
            "consent_id": "consent-a",
            "limit": 3,
        }
    ]


def test_consent_revocation_cascade_surfaces_remote_unresolved_without_claiming_deletion(
    monkeypatch,
) -> None:
    service = SpeechEvidenceRevocationService(
        evidence=_ConsentWorklist(("evidence-a",))  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        service,
        "revoke",
        lambda *_args, **_kwargs: SimpleNamespace(
            idempotent_replay=True,
            unresolved=(),
            remote_state="unresolved",
        ),
    )

    result = service.revoke_consent(
        VoicePrincipal("tenant-a", "owner-a"),
        "consent-a",
        expected_consent_version=2,
    )

    assert result.revoked_count == 1
    assert result.replayed_count == 1
    assert result.unresolved_count == 1
    assert result.reason_codes == ("speech_revocation_downstream_unresolved",)
