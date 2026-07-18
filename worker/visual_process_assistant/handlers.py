"""Narrow worker execution boundary for Visual Process assistance.

The handlers consume exactly one authenticated Hub task envelope.  They never
create tasks, route work, persist conversations or mutate a workflow graph.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Mapping
from typing import Any

from ananta_contracts.retrieval import RetrievalRequest, SourceRef
from ananta_contracts.visual_process_assistant import (
    ASSISTANT_CONTEXT_POLICY_VERSION,
    ASSISTANT_INFERENCE_JOB_VERSION,
    ASSISTANT_INFERENCE_RESULT_VERSION,
    ASSISTANT_RETRIEVAL_JOB_VERSION,
    ASSISTANT_RETRIEVAL_RESULT_VERSION,
    HELP_RESPONSE_VERSION,
    AssistantLocation,
    EvidenceRef,
    HelpResponse,
    TrustLevel,
    VerificationStatus,
)
from worker.core.model_provider import WorkerModelProvider
from worker.retrieval.codecompass_retriever import CodeCompassRetriever
from worker.visual_process_assistant.evidence_gate import (
    VisualProcessEvidenceConflictDetector,
    VisualProcessEvidenceReleaseGate,
)

RETRIEVAL_JOB_SCHEMA = ASSISTANT_RETRIEVAL_JOB_VERSION
RETRIEVAL_RESULT_SCHEMA = ASSISTANT_RETRIEVAL_RESULT_VERSION
INFERENCE_JOB_SCHEMA = ASSISTANT_INFERENCE_JOB_VERSION
INFERENCE_RESULT_SCHEMA = ASSISTANT_INFERENCE_RESULT_VERSION


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = dict(value)
    payload.pop("envelope_hash", None)
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _validate_hub_envelope(envelope: Mapping[str, Any], *, schema: str) -> dict[str, Any]:
    payload = dict(envelope or {})
    if str(payload.get("schema") or "") != schema:
        raise ValueError("assistant_worker_envelope_schema_invalid")
    authorization = dict(payload.get("hub_authorization") or {})
    if (
        authorization.get("issuer") != "ananta-hub"
        or authorization.get("transport") != "authenticated_hub_task_queue"
        or not str(authorization.get("task_id") or "")
    ):
        raise ValueError("assistant_worker_hub_authorization_required")
    supplied_hash = str(payload.get("envelope_hash") or "")
    if supplied_hash != _canonical_hash(payload):
        raise ValueError("assistant_worker_envelope_hash_mismatch")
    if int(payload.get("context_policy_version") or 0) != ASSISTANT_CONTEXT_POLICY_VERSION:
        raise ValueError("assistant_worker_context_policy_version_invalid")
    if str(payload.get("model_scope") or "") != "local_model":
        raise ValueError("assistant_worker_model_scope_denied")
    for field_name in (
        "context_id",
        "repository_revision",
        "codecompass_manifest_hash",
        "source_allowlist_version",
    ):
        if not str(payload.get(field_name) or "").strip():
            raise ValueError(f"assistant_worker_{field_name}_required")
    if schema == RETRIEVAL_JOB_SCHEMA:
        max_evidence_items = int(payload.get("max_evidence_items") or 0)
        if not 1 <= max_evidence_items <= 12:
            raise ValueError("assistant_worker_evidence_budget_invalid")
    elif schema == INFERENCE_JOB_SCHEMA:
        max_prompt_tokens = int(payload.get("max_prompt_tokens") or 0)
        estimated_prompt_tokens = int(payload.get("estimated_prompt_tokens") or 0)
        prompt_tokens = max(1, math.ceil(len(str(payload.get("prompt") or "")) / 4))
        if max_prompt_tokens <= 0 or estimated_prompt_tokens != prompt_tokens or prompt_tokens > max_prompt_tokens:
            raise ValueError("assistant_worker_prompt_budget_invalid")
    deadline = float(payload.get("deadline_at") or 0)
    if deadline and time.time() > deadline:
        raise TimeoutError("assistant_worker_deadline_exceeded")
    return payload


def _task_bound_envelope(
    task: Mapping[str, Any],
    *,
    expected_task_kind: str,
) -> dict[str, Any]:
    """Extract an envelope only from its authenticated queue task boundary.

    The queue transport authenticates the outer task.  Binding its immutable
    identity and registered kind to the nested authorization prevents a valid
    envelope from being replayed under a different Hub task or handler.
    """

    task_payload = dict(task or {})
    task_id = str(task_payload.get("id") or "").strip()
    if not task_id:
        raise ValueError("assistant_worker_task_id_required")
    if str(task_payload.get("task_kind") or "").strip() != expected_task_kind:
        raise ValueError("assistant_worker_task_kind_mismatch")
    execution_context = task_payload.get("worker_execution_context")
    if not isinstance(execution_context, Mapping):
        raise ValueError("assistant_worker_task_context_required")
    envelope = execution_context.get("visual_process_assistant_job")
    if not isinstance(envelope, Mapping):
        raise ValueError("assistant_worker_task_envelope_required")
    authorization = envelope.get("hub_authorization")
    if not isinstance(authorization, Mapping) or str(authorization.get("task_id") or "") != task_id:
        raise ValueError("assistant_worker_task_binding_mismatch")
    return dict(envelope)


class VisualProcessAssistantRetrievalHandler:
    """Retrieve and scan evidence once; orchestration remains in the Hub."""

    def __init__(
        self,
        retriever: CodeCompassRetriever | None = None,
        evidence_gate: VisualProcessEvidenceReleaseGate | None = None,
        conflict_detector: VisualProcessEvidenceConflictDetector | None = None,
    ) -> None:
        self._retriever = retriever or CodeCompassRetriever(scope="visual_process_assistant")
        self._evidence_gate = evidence_gate or VisualProcessEvidenceReleaseGate()
        self._conflict_detector = conflict_detector or VisualProcessEvidenceConflictDetector()

    def execute(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        job = _validate_hub_envelope(envelope, schema=RETRIEVAL_JOB_SCHEMA)
        refs = tuple(SourceRef.from_mapping(item) for item in list(job.get("allowed_source_refs") or []))
        tenant_id = str(job.get("tenant_id") or "")
        scope = str(job.get("source_scope") or "")
        if any(ref.tenant_id != tenant_id or ref.scope != scope for ref in refs):
            raise ValueError("assistant_worker_source_scope_mismatch")
        result = self._retriever.retrieve(
            RetrievalRequest(
                query=str(job.get("question") or ""),
                tenant_id=tenant_id,
                scope=scope,
                allowed_source_ids=frozenset(ref.source_id for ref in refs),
                allowed_source_refs=refs,
                max_results=max(1, min(int(job.get("max_evidence_items") or 8), 12)),
                repository_revision=str(job.get("repository_revision") or ""),
                manifest_hash=str(job.get("codecompass_manifest_hash") or ""),
                source_allowlist_version=str(job.get("source_allowlist_version") or ""),
            )
        )
        evidence: list[dict[str, Any]] = []
        rejected = list(result.rejection_reasons)
        release_rejected_count = 0
        blocked_stubs: list[dict[str, Any]] = []
        detected_conflicts = self._conflict_detector.detect(result.sources)
        for source in result.sources:
            release = self._evidence_gate.release(
                source,
                model_scope=str(job.get("model_scope") or "none"),
            )
            if not release.allowed:
                rejected.extend(release.reason_codes)
                release_rejected_count += 1
                if release.safe_stub:
                    blocked_stubs.append(
                        {
                            "source_id": source.source_id,
                            "reason_codes": list(release.reason_codes),
                            "safe_stub": release.safe_stub,
                        }
                    )
                continue
            if source.source_ref is None:
                rejected.append("source_ref_missing")
                release_rejected_count += 1
                continue
            provenance = dict(source.provenance or {})
            line_start = _positive_int(provenance.get("line_start"))
            line_end = _positive_int(provenance.get("line_end"))
            evidence.append(
                EvidenceRef(
                    # Preserve the authority-issued identity end-to-end; path,
                    # hash or list position must never become an evidence id.
                    evidence_id=source.source_id,
                    source_id=source.source_id,
                    source_version=source.source_version,
                    tenant_id=source.tenant_id,
                    scope=source.scope,
                    provenance_digest=source.source_ref.provenance_digest,
                    path=source.path or None,
                    line_start=line_start,
                    line_end=line_end if line_start is not None else None,
                    trust_level=TrustLevel.extracted,
                    verification_status=VerificationStatus.verified,
                    excerpt=release.content[:4000],
                ).model_dump(mode="json")
            )
        accepted_source_ids = {str(item.get("source_id") or "") for item in evidence}
        evidence_conflicts = [
            {
                "conflict_key": conflict.conflict_key,
                "source_ids": list(conflict.source_ids),
                "reason_code": "evidence_conflict",
            }
            for conflict in detected_conflicts
            if len(conflict.source_ids) > 1 and set(conflict.source_ids).issubset(accepted_source_ids)
        ]
        conflicted_source_ids = {
            source_id for conflict in evidence_conflicts for source_id in list(conflict["source_ids"])
        }
        for item in evidence:
            if item.get("source_id") in conflicted_source_ids:
                item["reason_codes"] = sorted({*list(item.get("reason_codes") or []), "evidence_conflict"})

        consistency_state = str(result.metadata.get("consistency_state") or "degraded")
        if evidence_conflicts:
            consistency_state = "conflict"
            rejected.append("evidence_conflict")
        elif consistency_state == "current" and release_rejected_count:
            consistency_state = "rejected"
        elif not evidence and ("no_results" in rejected or "production_channel_empty" in rejected):
            consistency_state = "no_results"
        return {
            "schema": RETRIEVAL_RESULT_SCHEMA,
            "task_id": str(job["hub_authorization"]["task_id"]),
            "request_id": str(job.get("request_id") or ""),
            "context_id": str(job.get("context_id") or ""),
            "status": "completed",
            "evidence": evidence,
            "rejected_count": int(result.rejected_count) + release_rejected_count,
            "rejection_reasons": sorted(set(rejected)),
            "consistency_state": consistency_state,
            "blocked_stubs": sorted(blocked_stubs, key=lambda item: item["source_id"]),
            "evidence_conflicts": evidence_conflicts,
        }

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        envelope = _task_bound_envelope(
            dict(kwargs.get("task") or {}),
            expected_task_kind="visual_process_assistant_retrieval",
        )
        return {"worker_result": self.execute(envelope), "command": None, "tool_calls": []}


class VisualProcessAssistantInferenceHandler:
    """Call one worker-local model and validate its typed response."""

    def __init__(self, model_provider: WorkerModelProvider | None) -> None:
        self._model_provider = model_provider

    def execute(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        job = _validate_hub_envelope(envelope, schema=INFERENCE_JOB_SCHEMA)
        prompt = str(job.get("prompt") or "")
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if prompt_hash != str(job.get("prompt_hash") or ""):
            raise ValueError("assistant_worker_prompt_hash_mismatch")
        response, reason_code, model_metadata = self._model_response(job, prompt)
        return {
            "schema": INFERENCE_RESULT_SCHEMA,
            "task_id": str(job["hub_authorization"]["task_id"]),
            "request_id": str(job.get("request_id") or ""),
            "context_id": str(job.get("context_id") or ""),
            "prompt_hash": prompt_hash,
            "status": "completed",
            "reason_code": reason_code,
            "response": response.model_dump(mode="json"),
            "model_metadata": _safe_model_metadata(model_metadata),
        }

    def _model_response(
        self,
        job: Mapping[str, Any],
        prompt: str,
    ) -> tuple[HelpResponse, str | None, dict[str, Any]]:
        if self._model_provider is None:
            return self._fallback(job, "model_provider_unavailable"), "model_provider_unavailable", {}
        try:
            raw = self._model_provider.complete(
                prompt=prompt,
                prompt_template_version=str(job.get("prompt_version") or ""),
            )
        except TimeoutError:
            raise
        except Exception as exc:  # Provider internals must not leak into the response.
            return self._fallback(job, "model_provider_failed"), f"model_provider_failed:{type(exc).__name__}", {}
        parsed = _parse_json_object(raw.text)
        try:
            response = HelpResponse.model_validate(parsed) if parsed is not None else None
            if response is None:
                raise ValueError("assistant_model_json_invalid")
            if response.context_id != str(job.get("context_id") or ""):
                raise ValueError("assistant_model_context_mismatch")
            if response.prompt_version != str(job.get("prompt_version") or ""):
                raise ValueError("assistant_model_prompt_version_mismatch")
            allowed_evidence = {
                str(item.get("evidence_id") or ""): EvidenceRef.model_validate(item)
                for item in list(job.get("approved_evidence") or [])
                if isinstance(item, Mapping)
            }
            if any(
                item.evidence_id not in allowed_evidence
                or item.model_dump(mode="json") != allowed_evidence[item.evidence_id].model_dump(mode="json")
                for item in response.evidence
            ):
                raise ValueError("assistant_model_evidence_forged")
            return response, None, dict(raw.metadata or {})
        except Exception:
            return self._fallback(job, "model_output_invalid"), "model_output_invalid", dict(raw.metadata or {})

    @staticmethod
    def _fallback(job: Mapping[str, Any], reason: str) -> HelpResponse:
        location = AssistantLocation.model_validate(dict(job.get("location") or {}))
        return HelpResponse(
            contract_version=HELP_RESPONSE_VERSION,
            context_id=str(job.get("context_id") or ""),
            prompt_version=str(job.get("prompt_version") or ""),
            summary="Die belegte Editorhilfe konnte keine valide Modellantwort erzeugen.",
            location=location,
            explanation=(
                "Der Workflow wurde nicht veraendert. Bitte die Frage erneut senden oder den Kontext aktualisieren."
            ),
            warnings=[reason],
            next_actions=["Kontext aktualisieren", "Frage erneut senden"],
            evidence=[],
            claims=[],
            workflow_patch=None,
        )

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        envelope = _task_bound_envelope(
            dict(kwargs.get("task") or {}),
            expected_task_kind="visual_process_assistant_inference",
        )
        return {"worker_result": self.execute(envelope), "command": None, "tool_calls": []}


def _parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = str(text or "").strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1])
            if candidate.lstrip().lower().startswith("json\n"):
                candidate = candidate.lstrip()[5:]
    try:
        parsed = json.loads(candidate)
    except (TypeError, ValueError):
        return None
    return dict(parsed) if isinstance(parsed, dict) else None


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _safe_model_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"provider", "model", "base_url_label", "timeout_seconds", "prompt_template_version", "llm_used"}
    return {str(key): value for key, value in dict(metadata or {}).items() if key in allowed}


__all__ = [
    "INFERENCE_JOB_SCHEMA",
    "INFERENCE_RESULT_SCHEMA",
    "RETRIEVAL_JOB_SCHEMA",
    "RETRIEVAL_RESULT_SCHEMA",
    "VisualProcessAssistantInferenceHandler",
    "VisualProcessAssistantRetrievalHandler",
]
