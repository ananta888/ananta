"""Hub acceptance of Visual Process Assistant worker results.

Workers return retrieval evidence and model answers as untrusted payloads.
This module validates their bindings to the Hub-issued task, request and
context, admits only Hub-authorized evidence, stores the reference-only
prompt context and projects content-free diagnostics onto the request row.
It never commits; the orchestrating service owns the transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from sqlmodel import Session

from agent.config import settings
from agent.db_models.visual_process_assistant import VisualProcessAssistantRequestDB
from agent.metrics import VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL
from agent.services.chat_session_security import ChatSessionPrincipal
from agent.services.visual_process_assistant_errors import VisualProcessAssistantError
from agent.services.visual_process_assistant_records import owned_context, store_context
from agent.services.visual_process_context_service import (
    VisualProcessContextService,
    VisualProcessPromptAssembly,
)
from ananta_contracts.visual_process_assistant import (
    ASSISTANT_INFERENCE_RESULT_VERSION,
    ASSISTANT_RETRIEVAL_RESULT_VERSION,
    EditorContextEnvelope,
    EvidenceRef,
    HelpResponse,
    VerificationStatus,
)

INFERENCE_RESULT_SCHEMA = ASSISTANT_INFERENCE_RESULT_VERSION
RETRIEVAL_RESULT_SCHEMA = ASSISTANT_RETRIEVAL_RESULT_VERSION


class VisualProcessAssistantResultAcceptance:
    """Validate and project worker results for one request row."""

    def __init__(
        self,
        *,
        context_service: VisualProcessContextService,
        clock: Callable[[], float],
        model_timeout_ms: int,
    ) -> None:
        self._contexts = context_service
        self._clock = clock
        self._model_timeout_ms = int(model_timeout_ms)

    def accept_retrieval(
        self,
        db: Session,
        row: VisualProcessAssistantRequestDB,
        task_id: str,
        payload: dict[str, Any],
    ) -> VisualProcessPromptAssembly:
        if str(payload.get("schema") or "") != RETRIEVAL_RESULT_SCHEMA:
            raise VisualProcessAssistantError("assistant_retrieval_result_schema_invalid")
        if str(payload.get("status") or "") != "completed":
            raise VisualProcessAssistantError("assistant_retrieval_result_status_invalid")
        self.validate_worker_binding(row, task_id, payload)
        source_context = owned_context(
            db,
            row.context_id,
            ChatSessionPrincipal.from_values(row.tenant_id, row.owner_subject),
        )
        context = EditorContextEnvelope.model_validate(source_context.context_json)
        allowed = {
            (
                item.source_id,
                item.source_version,
                item.tenant_id,
                item.scope,
                item.provenance_digest.removeprefix("sha256:") if item.provenance_digest else None,
            )
            for item in context.evidence_refs
            if item.verification_status == VerificationStatus.verified
        }
        allowed_source_ids = {str(identity[0]) for identity in allowed if identity[0]}
        consistency = str(payload.get("consistency_state") or "degraded")
        if consistency not in {
            "current",
            "degraded",
            "stale",
            "stale_context",
            "no_results",
            "rejected",
            "conflict",
        }:
            raise VisualProcessAssistantError("assistant_retrieval_consistency_state_invalid")
        rejection_reasons = sorted({str(item) for item in list(payload.get("rejection_reasons") or []) if str(item)})
        rejected_count = int(payload.get("rejected_count") or 0)
        blocked_sources = _validated_blocked_source_audit(
            payload.get("blocked_stubs"),
            allowed_source_ids=allowed_source_ids,
        )
        evidence_conflicts = _validated_evidence_conflicts(
            payload.get("evidence_conflicts"),
            allowed_source_ids=allowed_source_ids,
        )
        if bool(evidence_conflicts) != (consistency == "conflict"):
            raise VisualProcessAssistantError("assistant_retrieval_conflict_state_invalid")
        if consistency != "current":
            lifecycle_status = (
                "stale"
                if consistency in {"stale", "stale_context"}
                or any("stale" in reason or "revision_mismatch" in reason for reason in rejection_reasons)
                else "rejected"
            )
            VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status=lifecycle_status).inc()
        if rejected_count > 0:
            VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status="rejected").inc(rejected_count)
        accepted: list[EvidenceRef] = []
        if consistency in {"current", "conflict"}:
            for raw in list(payload.get("evidence") or []):
                try:
                    evidence = EvidenceRef.model_validate(raw)
                except (TypeError, ValueError) as exc:
                    raise VisualProcessAssistantError("assistant_retrieval_evidence_invalid") from exc
                identity = (
                    evidence.source_id,
                    evidence.source_version,
                    evidence.tenant_id,
                    evidence.scope,
                    evidence.provenance_digest.removeprefix("sha256:") if evidence.provenance_digest else None,
                )
                if identity not in allowed:
                    raise VisualProcessAssistantError("assistant_retrieval_evidence_not_allowed", status_code=403)
                if evidence.verification_status != VerificationStatus.verified:
                    raise VisualProcessAssistantError("assistant_retrieval_evidence_unverified")
                accepted.append(evidence)
        enriched = self._contexts.with_projected_evidence(
            context,
            accepted,
            budget_profile="conversation",
        )
        transient_evidence = list(enriched.evidence_refs)
        reference_evidence = [item.model_copy(update={"excerpt": None}) for item in transient_evidence]
        reference_context = enriched.model_copy(update={"evidence_refs": reference_evidence})
        reference_context.canonical_bytes()
        prompt_context_row = store_context(
            db,
            ChatSessionPrincipal.from_values(row.tenant_id, row.owner_subject),
            reference_context,
        )
        assembly = self._contexts.assemble_prompt(
            reference_context,
            question_text=row.question_text,
            evidence_override=transient_evidence,
        )
        if assembly.context_id != prompt_context_row.context_id:
            raise VisualProcessAssistantError("assistant_prompt_context_binding_invalid")
        prompt_evidence_ids = set(assembly.approved_evidence_refs)
        accepted_references = [item for item in reference_evidence if item.evidence_id in prompt_evidence_ids]
        row.prompt_context_id = prompt_context_row.context_id
        row.accepted_evidence_json = [item.model_dump(mode="json") for item in accepted_references]
        row.prompt_snapshot_json = {
            **assembly.as_dict(include_prompt=False),
            "retrieval_consistency_state": consistency,
            "retrieval_rejected_count": rejected_count,
            "retrieval_rejection_reasons": rejection_reasons,
            "retrieval_blocked_sources": blocked_sources,
            "retrieval_evidence_conflicts": evidence_conflicts,
        }
        row.status = "queued_inference"
        row.error_code = _retrieval_error_code(
            consistency=consistency,
            rejection_reasons=rejection_reasons,
            accepted_count=len(accepted_references),
        )
        row.inference_deadline_at = float(self._clock()) + self._model_timeout_ms / 1000.0
        row.updated_at = float(self._clock())
        db.add(row)
        return assembly

    def accept_inference(
        self,
        db: Session,
        row: VisualProcessAssistantRequestDB,
        task_id: str,
        payload: dict[str, Any],
    ) -> None:
        if str(payload.get("schema") or "") != INFERENCE_RESULT_SCHEMA:
            raise VisualProcessAssistantError("assistant_inference_result_schema_invalid")
        if str(payload.get("status") or "") != "completed":
            raise VisualProcessAssistantError("assistant_inference_result_status_invalid")
        self.validate_worker_binding(row, task_id, payload)
        prompt_hash = str((row.prompt_snapshot_json or {}).get("prompt_hash") or "")
        if str(payload.get("prompt_hash") or "") != prompt_hash:
            raise VisualProcessAssistantError("assistant_inference_prompt_hash_mismatch", status_code=409)
        response = HelpResponse.model_validate(payload.get("response") or {})
        if response.context_id != row.prompt_context_id or response.prompt_version != row.prompt_version:
            raise VisualProcessAssistantError("assistant_inference_context_mismatch", status_code=409)
        accepted_evidence = {
            str(item.get("evidence_id") or ""): EvidenceRef.model_validate(item) for item in row.accepted_evidence_json
        }
        if any(
            item.evidence_id not in accepted_evidence
            or item.model_dump(mode="json") != accepted_evidence[item.evidence_id].model_dump(mode="json")
            for item in response.evidence
        ):
            raise VisualProcessAssistantError("assistant_inference_evidence_forged", status_code=403)
        if response.workflow_patch is not None:
            if not settings.visual_process_ai_patches_enabled:
                raise VisualProcessAssistantError("assistant_patch_feature_disabled_in_result", status_code=403)
            consistency = str((row.prompt_snapshot_json or {}).get("retrieval_consistency_state") or "degraded")
            if consistency != "current":
                raise VisualProcessAssistantError(
                    "assistant_patch_evidence_not_current",
                    status_code=409,
                )
        row.response_json = response.model_dump(mode="json")
        row.status = "completed"
        # A successful inference does not make a degraded retrieval healthy.
        # Preserve the content-free retrieval state unless the Worker returns a
        # more specific inference diagnostic such as ``model_output_invalid``.
        row.error_code = str(payload.get("reason_code") or "") or row.error_code
        row.updated_at = float(self._clock())
        db.add(row)
        VISUAL_PROCESS_ASSISTANT_REQUESTS_TOTAL.labels(status="completed").inc()

    @staticmethod
    def validate_worker_binding(
        row: VisualProcessAssistantRequestDB,
        task_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if str(payload.get("task_id") or "") != task_id:
            raise VisualProcessAssistantError("assistant_worker_task_binding_mismatch", status_code=409)
        if str(payload.get("request_id") or "") != row.id:
            raise VisualProcessAssistantError("assistant_worker_request_binding_mismatch", status_code=409)
        expected_context = row.context_id if row.retrieval_task_id == task_id else row.prompt_context_id
        if str(payload.get("context_id") or "") != str(expected_context or ""):
            raise VisualProcessAssistantError("assistant_worker_context_binding_mismatch", status_code=409)


def _retrieval_error_code(
    *,
    consistency: str,
    rejection_reasons: list[str],
    accepted_count: int,
) -> str | None:
    """Project retrieval diagnostics to stable, content-free UI states."""

    state = str(consistency or "degraded").strip().lower()
    reasons = {str(reason or "").strip().lower() for reason in rejection_reasons}
    if state == "current" and accepted_count > 0:
        return None
    if state in {"stale", "stale_context"} or any(
        "stale" in reason or "revision_mismatch" in reason or "manifest_mismatch" in reason for reason in reasons
    ):
        return "assistant_stale_context"
    if state == "conflict" or "evidence_conflict" in reasons:
        return "assistant_evidence_conflict"
    if (
        state == "no_results"
        or not accepted_count
        and any(
            reason
            in {
                "no_results",
                "production_channel_empty",
                "retrieval_provider_unconfigured",
            }
            for reason in reasons
        )
    ):
        return "assistant_no_results"
    if state == "rejected" or reasons:
        return "assistant_evidence_rejected"
    return "assistant_no_results" if accepted_count == 0 else "assistant_evidence_degraded"


def _validated_blocked_source_audit(
    raw_value: Any,
    *,
    allowed_source_ids: set[str],
) -> list[dict[str, Any]]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        raise VisualProcessAssistantError("assistant_retrieval_blocked_stubs_invalid")
    audit: list[dict[str, Any]] = []
    for raw in raw_value:
        if not isinstance(raw, Mapping) or set(raw) != {"source_id", "reason_codes", "safe_stub"}:
            raise VisualProcessAssistantError("assistant_retrieval_blocked_stub_invalid")
        source_id = str(raw.get("source_id") or "")
        reasons = sorted({str(item) for item in list(raw.get("reason_codes") or []) if str(item)})
        expected_stub = f"[REPOSITORY EVIDENCE BLOCKED] source_id={source_id} reasons={','.join(reasons)}"
        if (
            source_id not in allowed_source_ids
            or not reasons
            or len(reasons) > 20
            or str(raw.get("safe_stub") or "") != expected_stub
        ):
            raise VisualProcessAssistantError("assistant_retrieval_blocked_stub_invalid")
        audit.append({"source_id": source_id, "reason_codes": reasons})
    return sorted(audit, key=lambda item: item["source_id"])


def _validated_evidence_conflicts(
    raw_value: Any,
    *,
    allowed_source_ids: set[str],
) -> list[dict[str, Any]]:
    if raw_value is None:
        return []
    if not isinstance(raw_value, list):
        raise VisualProcessAssistantError("assistant_retrieval_evidence_conflicts_invalid")
    conflicts: list[dict[str, Any]] = []
    for raw in raw_value:
        if not isinstance(raw, Mapping) or set(raw) != {"conflict_key", "source_ids", "reason_code"}:
            raise VisualProcessAssistantError("assistant_retrieval_evidence_conflict_invalid")
        conflict_key = str(raw.get("conflict_key") or "").strip()
        source_ids = sorted({str(item) for item in list(raw.get("source_ids") or []) if str(item)})
        if (
            not conflict_key
            or len(conflict_key) > 200
            or any(not char.isalnum() and char not in "._:/-" for char in conflict_key)
            or len(source_ids) < 2
            or not set(source_ids).issubset(allowed_source_ids)
            or str(raw.get("reason_code") or "") != "evidence_conflict"
        ):
            raise VisualProcessAssistantError("assistant_retrieval_evidence_conflict_invalid")
        conflicts.append(
            {
                "conflict_key": conflict_key,
                "source_ids": source_ids,
                "reason_code": "evidence_conflict",
            }
        )
    return sorted(conflicts, key=lambda item: (item["conflict_key"], item["source_ids"]))


__all__ = [
    "INFERENCE_RESULT_SCHEMA",
    "RETRIEVAL_RESULT_SCHEMA",
    "VisualProcessAssistantResultAcceptance",
]
