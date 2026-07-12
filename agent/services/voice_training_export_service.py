from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.repositories.voice_result_artifact import VoiceResultArtifactRepository
from agent.services.voice_consent_service import VoiceConsentService, get_voice_consent_service
from agent.services.voice_governance_domain import VoiceGovernanceError, VoicePrincipal, validate_identifier
from agent.services.voice_personalization_service import (
    VoicePersonalizationService,
    get_voice_personalization_service,
)
from agent.services.voice_sensitive_text_codec import VoiceSensitiveTextCodec, get_voice_sensitive_text_codec


class VoiceTrainingExportService:
    """Materialize an encrypted training-data export from one approved Hub task.

    This service deliberately has no model or trainer dependency. It is a Hub
    fallback worker because the source data and encryption key must not leave
    the control plane merely to build a bounded JSON artifact.
    """

    def __init__(
        self,
        *,
        repository: VoiceResultArtifactRepository | None = None,
        personalization: VoicePersonalizationService | None = None,
        consent: VoiceConsentService | None = None,
        codec: VoiceSensitiveTextCodec | None = None,
        audit_sink: Callable[[str, dict[str, Any]], None] = log_audit,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository or VoiceResultArtifactRepository()
        self._personalization = personalization or get_voice_personalization_service()
        self._consent = consent or get_voice_consent_service()
        self._codec = codec or get_voice_sensitive_text_codec()
        self._audit = audit_sink
        self._clock = clock

    def execute_approved_task(self, principal: VoicePrincipal, task_id: str) -> dict[str, Any]:
        from agent.repository import task_repo
        from agent.services.task_runtime_service import update_local_task_status

        normalized_task_id = validate_identifier(task_id, field="task_id", max_length=200)
        task = task_repo.get_by_id(normalized_task_id)
        context = self._approved_context(task, principal)
        update_local_task_status(
            normalized_task_id,
            "in_progress",
            force=True,
            event_type="voice_training_export_started",
            event_actor="hub",
            event_details={"profile_id": context["profile_id"], "starts_training": False},
        )
        try:
            consent = self._consent.require_active(principal, context["profile_id"])
            if consent.id != context["consent_id"] or int(consent.version) != context["consent_version"]:
                raise VoiceGovernanceError(
                    code="voice_training_export.consent_changed",
                    message="voice consent changed after export approval",
                    status_code=409,
                )
            source = self._personalization.export(principal, context["profile_id"])
            payload = self._build_payload(
                task_id=normalized_task_id,
                context=context,
                consent=consent,
                source=source,
            )
            artifact = self._store(principal, context=context, payload=payload, retention_days=consent.retention_days)
        except Exception as exc:
            update_local_task_status(
                normalized_task_id,
                "failed",
                force=True,
                status_reason_code="voice_training_export_failed",
                status_reason_details={"error_type": type(exc).__name__},
                event_type="voice_training_export_failed",
                event_actor="hub",
                event_details={"profile_id": context["profile_id"]},
            )
            raise
        update_local_task_status(
            normalized_task_id,
            "completed",
            force=True,
            last_output=artifact["artifact_ref"],
            verification_status={
                "voice_training_export": {
                    "status": "verified",
                    "artifact_ref": artifact["artifact_ref"],
                    "starts_training": False,
                    "item_count": artifact["item_count"],
                }
            },
            event_type="voice_training_export_completed",
            event_actor="hub",
            event_details={
                "profile_id": context["profile_id"],
                "artifact_ref": artifact["artifact_ref"],
                "item_count": artifact["item_count"],
                "starts_training": False,
            },
        )
        self._audit(
            "voice_training_export_created",
            {
                "tenant_id": principal.tenant_id,
                "owner_subject": principal.subject,
                "profile_id": context["profile_id"],
                "task_id": normalized_task_id,
                "artifact_ref": artifact["artifact_ref"],
                "item_count": artifact["item_count"],
                "starts_training": False,
            },
        )
        return artifact

    def get(self, principal: VoicePrincipal, *, profile_id: str, artifact_id: str) -> dict[str, Any]:
        normalized_profile = validate_identifier(profile_id, field="profile_id")
        normalized_artifact = validate_identifier(artifact_id, field="artifact_id", max_length=200)
        artifact = self._repository.get(principal, normalized_artifact)
        if artifact is None or artifact.profile_id != normalized_profile or artifact.artifact_kind != "training_export":
            raise VoiceGovernanceError(
                code="voice_training_export.not_found",
                message="voice training export artifact not found",
                status_code=404,
            )
        if artifact.expires_at <= self._clock():
            raise VoiceGovernanceError(
                code="voice_training_export.expired",
                message="voice training export artifact expired",
                status_code=410,
            )
        payload = json.loads(self._codec.decrypt(artifact.payload_ciphertext) or "{}")
        canonical = self._canonical(payload)
        if hashlib.sha256(canonical).hexdigest() != artifact.payload_digest:
            raise VoiceGovernanceError(
                code="voice_training_export.integrity_failed",
                message="voice training export integrity verification failed",
                status_code=500,
            )
        return {
            "artifact_ref": artifact.id,
            "expires_at": artifact.expires_at,
            "payload_digest": artifact.payload_digest,
            "export": payload,
        }

    @staticmethod
    def _approved_context(task: Any, principal: VoicePrincipal) -> dict[str, Any]:
        raw = dict(getattr(task, "worker_execution_context", {}) or {}) if task is not None else {}
        context = dict(raw.get("voice_training_export") or {})
        valid = (
            task is not None
            and getattr(task, "task_kind", None) == "voice_training_export"
            and context.get("origin") == "explicit_user_approval"
            and context.get("starts_training") is False
            and context.get("tenant_id") == principal.tenant_id
            and context.get("owner_subject") == principal.subject
        )
        if not valid:
            raise VoiceGovernanceError(
                code="voice_training_export.task_not_approved",
                message="an explicit tenant-bound Hub export task is required",
                status_code=403,
            )
        return {
            "profile_id": validate_identifier(context.get("profile_id"), field="profile_id"),
            "purpose": str(context.get("purpose") or "")[:200],
            "license": str(context.get("license") or "")[:160],
            "consent_id": validate_identifier(context.get("consent_id"), field="consent_id"),
            "consent_version": int(context.get("consent_version") or 0),
        }

    @staticmethod
    def _build_payload(
        *,
        task_id: str,
        context: dict[str, Any],
        consent: Any,
        source: dict[str, Any],
    ) -> dict[str, Any]:
        items = [
            {
                "kind": item.get("kind"),
                "source_text": item.get("source_text"),
                "target_text": item.get("target_text"),
                "metadata": dict(item.get("metadata") or {}),
                "provenance": {
                    "feedback_id": item.get("id"),
                    "review_id": item.get("source_review_id"),
                    "consent_id": item.get("consent_id"),
                    "consent_version": item.get("consent_version"),
                },
            }
            for item in list(source.get("items") or [])
        ]
        return {
            "schema_version": "ananta.voice-training-export.v1",
            "profile_id": context["profile_id"],
            "purpose": context["purpose"],
            "license": context["license"],
            "consent": {
                "id": consent.id,
                "version": int(consent.version),
                "categories": sorted(consent.categories or []),
                "retention_days": int(consent.retention_days),
            },
            "provenance": {
                "origin": "explicit_user_approved_hub_task",
                "task_id": task_id,
                "source_schema": source.get("schema_version"),
                "source_profile_version": source.get("version"),
            },
            "deletion": {"profile_id": context["profile_id"], "delete_with_profile": True},
            "items": items,
            "starts_training": False,
        }

    def _store(
        self,
        principal: VoicePrincipal,
        *,
        context: dict[str, Any],
        payload: dict[str, Any],
        retention_days: int,
    ) -> dict[str, Any]:
        canonical = self._canonical(payload)
        if len(canonical) > 2 * 1024 * 1024:
            raise VoiceGovernanceError(
                code="voice_training_export.too_large",
                message="voice training export exceeds its size budget",
                status_code=413,
            )
        request_hash = hashlib.sha256(self._canonical({key: context[key] for key in sorted(context)})).hexdigest()
        expires_at = self._clock() + max(1, min(int(retention_days), 30)) * 86_400
        payload_ciphertext = self._codec.encrypt(canonical.decode("utf-8"))
        if payload_ciphertext is None:
            raise VoiceGovernanceError(
                code="voice_training_export.encryption_failed",
                message="voice training export encryption returned no ciphertext",
                status_code=500,
            )
        artifact = self._repository.create(
            principal,
            request_hash=request_hash,
            profile_id=context["profile_id"],
            artifact_kind="training_export",
            parent_artifact_id=None,
            payload_ciphertext=payload_ciphertext,
            payload_digest=hashlib.sha256(canonical).hexdigest(),
            candidate_ids=[],
            expires_at=expires_at,
        )
        return {
            "artifact_ref": artifact.id,
            "payload_digest": artifact.payload_digest,
            "expires_at": artifact.expires_at,
            "item_count": len(payload["items"]),
            "starts_training": False,
        }

    @staticmethod
    def _canonical(payload: Any) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


voice_training_export_service = VoiceTrainingExportService()


def get_voice_training_export_service() -> VoiceTrainingExportService:
    return voice_training_export_service
