"""Fail-closed release policy for repository evidence entering assistant prompts."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from ananta_contracts.visual_process_assistant import ASSISTANT_CONTEXT_POLICY_VERSION
from worker.core.context_access_policy import (
    ContextAccessPolicy,
    ContextAccessPolicyEvaluator,
    ContextAccessRule,
    Decision,
    DestinationContext,
    ModelScope,
    RequestedOperation,
    Sensitivity,
    SourceType,
)
from worker.core.context_resolver import ContextBlock, ContextSensitivity
from worker.core.context_scanner import ContextScanner
from worker.core.redaction import enforce_redaction_gate, redact_text


class EvidenceSource(Protocol):
    source_id: str
    source_version: str
    path: str
    content: str
    provenance: dict


@dataclass(frozen=True, slots=True)
class EvidenceReleaseDecision:
    allowed: bool
    content: str = ""
    reason_codes: tuple[str, ...] = ()
    safe_stub: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceConflict:
    conflict_key: str
    source_ids: tuple[str, ...]


class VisualProcessEvidenceConflictDetector:
    """Detect explicit doc-vs-contract conflicts without reading raw content.

    Indexers opt in by supplying a stable conflict key and an assertion digest.
    The detector never guesses contradictions from textual similarity.
    """

    _DOCUMENT_KINDS = frozenset(
        {
            "documentation",
            "document",
            "md_document",
            "md_heading",
            "markdown",
            "rst_document",
        }
    )
    _AUTHORITATIVE_MARKERS = ("code", "schema", "symbol", "registry", "contract")

    def detect(self, sources: Iterable[EvidenceSource]) -> tuple[EvidenceConflict, ...]:
        grouped: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
        for source in sources:
            provenance = dict(source.provenance or {})
            conflict_key = str(provenance.get("evidence_conflict_key") or "").strip()
            assertion_digest = str(provenance.get("assertion_digest") or "").strip().lower()
            record_kind = str(provenance.get("record_kind") or "").strip().lower()
            if (
                not conflict_key
                or len(assertion_digest) != 64
                or any(char not in "0123456789abcdef" for char in assertion_digest)
            ):
                continue
            grouped[conflict_key].append((source.source_id, assertion_digest, record_kind))

        conflicts: list[EvidenceConflict] = []
        for conflict_key, entries in sorted(grouped.items()):
            digests = {digest for _source_id, digest, _kind in entries}
            kinds = {kind for _source_id, _digest, kind in entries}
            has_document = any(kind in self._DOCUMENT_KINDS or kind.startswith(("md_", "doc_")) for kind in kinds)
            has_authority = any(
                any(marker in kind for marker in self._AUTHORITATIVE_MARKERS) and kind not in self._DOCUMENT_KINDS
                for kind in kinds
            )
            if len(digests) > 1 and has_document and has_authority:
                conflicts.append(
                    EvidenceConflict(
                        conflict_key=conflict_key,
                        source_ids=tuple(sorted({source_id for source_id, _digest, _kind in entries})),
                    )
                )
        return tuple(conflicts)


class VisualProcessEvidenceReleaseGate:
    """Apply access policy, secret redaction and injection scanning in order.

    The gate intentionally exposes only stable reason codes and a neutral stub
    for blocked material. Raw blocked content never becomes evidence or audit
    metadata.
    """

    POLICY_VERSION = ASSISTANT_CONTEXT_POLICY_VERSION

    def __init__(
        self,
        *,
        evaluator: ContextAccessPolicyEvaluator | None = None,
        scanner: ContextScanner | None = None,
    ) -> None:
        self._evaluator = evaluator or ContextAccessPolicyEvaluator(_default_policy())
        self._scanner = scanner or ContextScanner()

    def release(
        self,
        source: EvidenceSource,
        *,
        model_scope: str,
    ) -> EvidenceReleaseDecision:
        scope = _model_scope(model_scope)
        sensitivity = _sensitivity(source.provenance.get("sensitivity"))
        if sensitivity not in {
            Sensitivity.public,
            Sensitivity.project_internal,
            Sensitivity.customer_confidential,
            Sensitivity.generated_summary,
        }:
            return _blocked(source.source_id, "sensitivity_denied")
        destination = DestinationContext(
            worker_id="delegated-worker",
            worker_kind="visual_process_assistant",
            runtime_target_id="visual_process_assistant",
            runtime_kind="worker",
            provider_id="worker_model_provider",
            provider_location="local" if scope == ModelScope.local_model else "remote",
            model_id="policy-bound-model",
            model_scope=scope,
            cloud_effective=scope in {ModelScope.approved_cloud, ModelScope.public_cloud},
            external_effective=scope not in {ModelScope.local_model, ModelScope.local_tool_only},
            local_effective=scope in {ModelScope.local_model, ModelScope.local_tool_only},
            requested_operation=RequestedOperation.send_to_llm,
            task_kind="visual_process_assistant_inference",
        )
        decision = self._evaluator.get_decision(
            {
                "block_id": source.source_id,
                "source_ref": f"{source.source_id}@{source.source_version}",
                "source_type": SourceType.codecompass_code.value,
                "sensitivity": sensitivity,
                "content_hash": hashlib.sha256(source.content.encode("utf-8")).hexdigest(),
            },
            destination,
        )
        if decision.decision not in {Decision.allow, Decision.allow_redacted}:
            reason = decision.reason_code.value if decision.reason_code is not None else "context_policy_denied"
            return _blocked(source.source_id, reason)

        secret_free, _markers = enforce_redaction_gate(source.content)
        redacted = redact_text(source.content)
        if not secret_free:
            return _blocked(source.source_id, "secret_detected")

        scan = self._scanner.scan(
            ContextBlock(
                source_type="repository_evidence",
                origin_id=source.source_id,
                provenance="codecompass_retrieval",
                sensitivity=ContextSensitivity.project_internal,
                content=redacted,
                token_estimate=max(1, len(redacted) // 4),
            )
        )
        if not scan.clean:
            reasons = tuple(sorted({f"prompt_injection_blocked:{finding.pattern_name}" for finding in scan.findings}))
            return EvidenceReleaseDecision(
                allowed=False,
                reason_codes=reasons,
                safe_stub=_safe_stub(source.source_id, reasons),
            )
        return EvidenceReleaseDecision(allowed=True, content=redacted)


def _default_policy() -> ContextAccessPolicy:
    return ContextAccessPolicy(
        policy_id="visual-process-assistant-evidence-release",
        version=VisualProcessEvidenceReleaseGate.POLICY_VERSION,
        scope="system_default",
        rules=[
            ContextAccessRule(
                id="verified-codecompass-to-local-assistant",
                description="Only non-sensitive CodeCompass evidence may enter a local assistant model.",
                source_types=[SourceType.codecompass_code, SourceType.codecompass_graph],
                allowed_worker_kinds=["visual_process_assistant"],
                allowed_runtime_kinds=["worker"],
                allowed_model_scopes=[ModelScope.local_model],
                send_allowed=True,
                redaction_required=True,
            )
        ],
        defaults={"send_allowed": False},
        validation_state="active",
    )


def _model_scope(value: str) -> ModelScope:
    try:
        return ModelScope(str(value or ""))
    except ValueError:
        return ModelScope.none


def _sensitivity(value: object) -> Sensitivity:
    normalized = str(value or "project_internal").strip().lower()
    aliases = {"internal": "project_internal", "internal_high": "security_sensitive"}
    try:
        return Sensitivity(aliases.get(normalized, normalized))
    except ValueError:
        return Sensitivity.unknown


def _blocked(source_id: str, reason: str) -> EvidenceReleaseDecision:
    reasons = (str(reason),)
    return EvidenceReleaseDecision(
        allowed=False,
        reason_codes=reasons,
        safe_stub=_safe_stub(source_id, reasons),
    )


def _safe_stub(source_id: str, reasons: tuple[str, ...]) -> str:
    return f"[REPOSITORY EVIDENCE BLOCKED] source_id={source_id} reasons={','.join(sorted(reasons))}"


__all__ = [
    "EvidenceConflict",
    "EvidenceReleaseDecision",
    "VisualProcessEvidenceConflictDetector",
    "VisualProcessEvidenceReleaseGate",
]
