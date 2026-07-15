from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.services.voice_delegation_task_service import VoiceDelegationTask
from agent.services.voice_generative_corrector_service import (
    get_voice_generative_corrector_service,
)
from agent.services.voice_generative_judge_service import get_voice_generative_judge_service
from agent.services.voice_governance_domain import VoicePrincipal
from agent.services.voice_restricted_choice_service import (
    get_voice_restricted_choice_service,
    new_voice_choice_run_id,
    voice_choice_policy_hash,
)


@dataclass(frozen=True)
class VoiceTranscriptionPostprocessOutcome:
    result: Mapping[str, Any]
    choice_applied: bool = False
    choice_reason: str = "restricted_choice_disabled"
    choice_manifest_digest: str = ""
    corrector_applied: bool = False
    corrector_reason: str = "generative_corrector_disabled"


class VoiceTranscriptionPostprocessingService:
    """Apply one Hub-selected optional correction strategy to a transcript."""

    def apply(
        self,
        result: Mapping[str, Any],
        effective_configuration: Mapping[str, Any],
        delegation: VoiceDelegationTask,
        *,
        principal: VoicePrincipal,
        request_id: str,
        language: str | None,
        run_id: str | None = None,
        restricted_choice_service: Any | None = None,
        generative_judge_service: Any | None = None,
        generative_corrector_service: Any | None = None,
    ) -> VoiceTranscriptionPostprocessOutcome:
        processed: Mapping[str, Any] = result
        choice_applied = False
        choice_reason = "restricted_choice_disabled"
        choice_manifest_digest = ""
        corrector_applied = False
        corrector_reason = "generative_corrector_disabled"
        feature_flags = effective_configuration.get("feature_flags")
        if (
            effective_configuration.get("correction_policy") == "restricted_choice"
            and isinstance(feature_flags, dict)
            and feature_flags.get("restricted_worker") is True
        ):
            try:
                choice_outcome = (restricted_choice_service or get_voice_restricted_choice_service()).apply(
                    processed,
                    effective_configuration=dict(effective_configuration),
                    tenant_id=principal.tenant_id,
                    task_id=delegation.task_id,
                    run_id=str(run_id or new_voice_choice_run_id()),
                    request_id=request_id,
                    deadline_epoch_ms=delegation.deadline_epoch_ms,
                    policy_hash=voice_choice_policy_hash(effective_configuration),
                )
                processed = choice_outcome.result
                choice_applied = choice_outcome.applied
                choice_reason = choice_outcome.reason_code
                choice_manifest_digest = choice_outcome.manifest_digest
            except Exception:
                # Optional restricted choice remains fail-open to the exact
                # baseline transcript, matching the established Voice API.
                choice_reason = "restricted_choice_hook_failed"
        elif (
            effective_configuration.get("correction_policy") == "generative_local"
            and isinstance(feature_flags, dict)
            and feature_flags.get("generative_judge") is True
        ):
            judge_outcome = (generative_judge_service or get_voice_generative_judge_service()).apply(
                processed,
                effective_configuration=dict(effective_configuration),
                tenant_id=principal.tenant_id,
                parent_task_id=delegation.task_id,
                request_id=request_id,
                deadline_epoch_ms=delegation.deadline_epoch_ms,
            )
            processed = judge_outcome.result
            choice_applied = judge_outcome.applied
            choice_reason = judge_outcome.reason_code
        elif (
            effective_configuration.get("correction_policy") == "generative_rewrite"
            and isinstance(feature_flags, dict)
            and feature_flags.get("generative_corrector") is True
        ):
            corrector_outcome = (generative_corrector_service or get_voice_generative_corrector_service()).apply(
                processed,
                effective_configuration=dict(effective_configuration),
                tenant_id=principal.tenant_id,
                parent_task_id=delegation.task_id,
                request_id=request_id,
                language=language,
                deadline_epoch_ms=delegation.deadline_epoch_ms,
            )
            processed = corrector_outcome.result
            corrector_applied = corrector_outcome.applied
            corrector_reason = corrector_outcome.reason_code
        return VoiceTranscriptionPostprocessOutcome(
            result=processed,
            choice_applied=choice_applied,
            choice_reason=choice_reason,
            choice_manifest_digest=choice_manifest_digest,
            corrector_applied=corrector_applied,
            corrector_reason=corrector_reason,
        )


voice_transcription_postprocessing_service = VoiceTranscriptionPostprocessingService()


def get_voice_transcription_postprocessing_service() -> VoiceTranscriptionPostprocessingService:
    return voice_transcription_postprocessing_service
