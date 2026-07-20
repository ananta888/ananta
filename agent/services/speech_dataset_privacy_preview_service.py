"""Content-minimizing privacy preview for governed speech datasets."""

from __future__ import annotations

import re
from collections import Counter
from typing import Mapping, Protocol

from agent.services.ml_intern_speech_dataset_build_service import MlInternSpeechDatasetBuildService
from agent.services.voice_governance_domain import VoicePrincipal

_REASON_CODE = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")


class SpeechPreviewGrantPort(Protocol):
    def authorize_raw_audio_preview(
        self,
        *,
        tenant_id: str,
        owner_subject: str,
        dataset_digest: str,
        grant_ref: str,
    ) -> bool: ...


class UnavailableSpeechPreviewGrantPort:
    def authorize_raw_audio_preview(self, **_kwargs: object) -> bool:
        return False


class SpeechDatasetPrivacyPreviewError(RuntimeError):
    def __init__(self, reason_code: str, *, status_code: int = 422) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


class SpeechDatasetPrivacyPreviewService:
    def __init__(
        self,
        grants: SpeechPreviewGrantPort | None = None,
        *,
        manifest_validator: MlInternSpeechDatasetBuildService | None = None,
    ) -> None:
        self._grants = grants or UnavailableSpeechPreviewGrantPort()
        self._validator = manifest_validator or MlInternSpeechDatasetBuildService()

    def preview(
        self,
        principal: VoicePrincipal,
        manifest: Mapping[str, object],
        *,
        admission_findings: Mapping[str, Mapping[str, object]] | None = None,
        include_raw_audio: bool = False,
        raw_audio_preview_grant_ref: str | None = None,
        raw_audio_refs: Mapping[str, str] | None = None,
    ) -> dict[str, object]:
        self._validator.validate(manifest)
        records = [dict(record) for record in manifest.get("records") or []]
        if len(records) > 10_000:
            raise SpeechDatasetPrivacyPreviewError("speech_preview_record_limit_exceeded")
        data_classes = Counter(str(item) for record in records for item in list(record.get("data_classes") or []))
        contributors = Counter(str(item) for record in records for item in list(record.get("contributors") or []))
        grant_refs = Counter(
            str(ref.get("consent_id"))
            for record in records
            for ref in list(record.get("consent_refs") or [])
            if isinstance(ref, Mapping)
        )
        findings = Counter()
        quarantine_count = 0
        reports = dict(admission_findings or {})
        if len(reports) > 10_000:
            raise SpeechDatasetPrivacyPreviewError("speech_preview_findings_limit_exceeded")
        for report in reports.values():
            decision = str(report.get("decision") or "unknown")
            if decision not in {"admitted", "quarantined", "rejected", "unknown"}:
                raise SpeechDatasetPrivacyPreviewError("speech_preview_finding_invalid")
            if decision == "quarantined":
                quarantine_count += 1
            reason_codes = list(report.get("reason_codes") or [])
            if len(reason_codes) > 32 or any(_REASON_CODE.fullmatch(str(code)) is None for code in reason_codes):
                raise SpeechDatasetPrivacyPreviewError("speech_preview_finding_invalid")
            findings.update(str(code) for code in reason_codes)
        result: dict[str, object] = {
            "schema": "ananta.speech-dataset-privacy-preview.v1",
            "dataset_id": manifest.get("dataset_id"),
            "manifest_digest": manifest.get("manifest_digest"),
            "record_count": len(records),
            "total_duration_ms": sum(int(record.get("duration_ms") or 0) for record in records),
            "data_class_counts": dict(sorted(data_classes.items())),
            "contributor_scopes": dict(sorted(contributors.items())),
            "grant_ref_counts": dict(sorted(grant_refs.items())),
            "quarantine_count": quarantine_count,
            "scan_findings": dict(sorted(findings.items())),
            "raw_audio_preview": {"authorized": False, "refs": []},
        }
        if not include_raw_audio:
            return result
        grant_ref = str(raw_audio_preview_grant_ref or "")
        if not self._grants.authorize_raw_audio_preview(
            tenant_id=principal.tenant_id,
            owner_subject=principal.subject,
            dataset_digest=str(manifest.get("manifest_digest") or ""),
            grant_ref=grant_ref,
        ):
            raise SpeechDatasetPrivacyPreviewError("speech_preview_raw_audio_grant_missing", status_code=403)
        refs: list[str] = []
        if len(raw_audio_refs or {}) > 10_000:
            raise SpeechDatasetPrivacyPreviewError("speech_preview_raw_audio_ref_limit_exceeded")
        for record in records:
            record_digest = str(record.get("record_digest") or "")
            ref = str((raw_audio_refs or {}).get(record_digest) or "")
            if "audio" in set(record.get("data_classes") or []):
                if not ref.startswith("artifact://speech-preview/") or ".." in ref.split("/"):
                    raise SpeechDatasetPrivacyPreviewError("speech_preview_raw_audio_ref_invalid")
                refs.append(ref)
        result["raw_audio_preview"] = {"authorized": True, "refs": refs[:100]}
        return result


__all__ = [
    "SpeechDatasetPrivacyPreviewError",
    "SpeechDatasetPrivacyPreviewService",
    "SpeechPreviewGrantPort",
    "UnavailableSpeechPreviewGrantPort",
]
