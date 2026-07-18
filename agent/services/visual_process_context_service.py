"""Deterministic editor context and prompt composition for the Hub."""

from __future__ import annotations

import copy
import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from agent.services.prompt_snapshot_service import PromptSnapshotService
from agent.visual_process.models import VisualProcessGraph
from ananta_contracts.visual_process_assistant import (
    AssistantLocation,
    EditorContextEnvelope,
    EvidenceRef,
    VerificationStatus,
    canonical_context_bytes,
    canonical_context_hash,
)

PROMPT_VERSION = "visual-process-assistant.v1"
PROMPT_TEMPLATE_REF = "visual-process-assistant-help-v1"
MAX_GRAPH_EXCERPT_STEPS = 50
MAX_GRAPH_EXCERPT_EDGES = 100


@dataclass(frozen=True, slots=True)
class VisualProcessContextBudget:
    """Deterministic limits for one semantic editor-context projection."""

    profile: Literal["selected", "conversation"]
    max_ranges: int
    max_lines_per_range: int
    max_prompt_tokens: int
    max_evidence_items: int


SELECTED_CONTEXT_BUDGET = VisualProcessContextBudget(
    profile="selected",
    max_ranges=4,
    max_lines_per_range=80,
    max_prompt_tokens=4_096,
    max_evidence_items=4,
)
CONVERSATION_CONTEXT_BUDGET = VisualProcessContextBudget(
    profile="conversation",
    max_ranges=8,
    max_lines_per_range=120,
    max_prompt_tokens=12_000,
    max_evidence_items=12,
)
_CONTEXT_BUDGETS = {
    SELECTED_CONTEXT_BUDGET.profile: SELECTED_CONTEXT_BUDGET,
    CONVERSATION_CONTEXT_BUDGET.profile: CONVERSATION_CONTEXT_BUDGET,
}


@dataclass(frozen=True, slots=True)
class EvidenceBudgetProjection:
    evidence: tuple[EvidenceRef, ...]
    discarded_count: int
    reason_counts: dict[str, int]
    truncated_range_count: int

    def audit_payload(self, budget: VisualProcessContextBudget) -> dict[str, Any]:
        return {
            "profile": budget.profile,
            "max_ranges": budget.max_ranges,
            "max_lines_per_range": budget.max_lines_per_range,
            "max_prompt_tokens": budget.max_prompt_tokens,
            "max_evidence_items": budget.max_evidence_items,
            "selected_evidence_count": len(self.evidence),
            "discarded_count": self.discarded_count,
            "discarded_reason_counts": dict(sorted(self.reason_counts.items())),
            "truncated_range_count": self.truncated_range_count,
            "truncation_reason_counts": (
                {"range_line_budget_truncated": self.truncated_range_count} if self.truncated_range_count else {}
            ),
        }


@dataclass(frozen=True)
class VisualProcessPromptAssembly:
    context_id: str
    prompt_version: str
    prompt_text: str
    prompt_hash: str
    prompt_snapshot: dict[str, Any]
    approved_evidence_refs: tuple[str, ...]
    rejected_evidence_count: int
    rejection_reasons: tuple[str, ...]
    estimated_prompt_tokens: int
    max_prompt_tokens: int

    def as_dict(self, *, include_prompt: bool = False) -> dict[str, Any]:
        payload = {
            "context_id": self.context_id,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "prompt_snapshot": copy.deepcopy(self.prompt_snapshot),
            "approved_evidence_refs": list(self.approved_evidence_refs),
            "rejected_evidence_count": self.rejected_evidence_count,
            "rejection_reasons": list(self.rejection_reasons),
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "max_prompt_tokens": self.max_prompt_tokens,
        }
        if include_prompt:
            payload["prompt_text"] = self.prompt_text
        return payload


class VisualProcessContextService:
    """Build bounded semantic context; never reads DOM or repository files."""

    def __init__(self, prompt_snapshots: PromptSnapshotService | None = None) -> None:
        self._prompt_snapshots = prompt_snapshots or PromptSnapshotService()

    def build_context(
        self,
        *,
        graph: VisualProcessGraph | dict[str, Any],
        location: AssistantLocation | dict[str, Any],
        editor_mode: str,
        repository_revision: str,
        codecompass_manifest_hash: str,
        source_allowlist_version: str,
        prompt_version: str = PROMPT_VERSION,
        locale: str = "de",
        draft_graph: VisualProcessGraph | dict[str, Any] | None = None,
        runtime_overlay: dict[str, Any] | None = None,
        validation_issues: Iterable[dict[str, Any]] = (),
        evidence_refs: Iterable[EvidenceRef | dict[str, Any]] = (),
        allowed_mutations: Iterable[str] = (),
        extensions: dict[str, Any] | None = None,
        budget_profile: Literal["selected", "conversation"] = "conversation",
    ) -> EditorContextEnvelope:
        definition = graph if isinstance(graph, VisualProcessGraph) else VisualProcessGraph.model_validate(graph)
        draft = (
            draft_graph
            if isinstance(draft_graph, VisualProcessGraph)
            else VisualProcessGraph.model_validate(draft_graph)
            if draft_graph is not None
            else definition
        )
        target = location if isinstance(location, AssistantLocation) else AssistantLocation.model_validate(location)
        if target.graph_id != definition.id:
            raise ValueError("editor_context_graph_id_mismatch")
        runtime_hash = canonical_context_hash(runtime_overlay) if runtime_overlay is not None else None
        budget = self.context_budget(budget_profile)
        refs = [item if isinstance(item, EvidenceRef) else EvidenceRef.model_validate(item) for item in evidence_refs]
        projection = self.project_evidence(refs, budget_profile=budget.profile)
        bounded_extensions = dict(extensions or {})
        bounded_extensions["ananta.context_budget"] = projection.audit_payload(budget)
        normalized_editor_mode = str(editor_mode).strip().lower()
        bounded_mutations = (
            []
            if normalized_editor_mode == "read_only"
            else sorted({str(item) for item in allowed_mutations if str(item).strip()})
        )
        envelope = EditorContextEnvelope(
            graph_id=definition.id,
            repository_revision=str(repository_revision),
            codecompass_manifest_hash=str(codecompass_manifest_hash),
            source_allowlist_version=str(source_allowlist_version),
            prompt_version=str(prompt_version),
            graph_schema_version=definition.graph_schema_version,
            node_registry_version=definition.node_registry_version,
            definition_revision=definition.definition_revision,
            definition_hash=definition.base_graph_hash or definition.definition_hash(),
            draft_hash=draft.definition_hash(),
            runtime_snapshot_hash=runtime_hash,
            editor_mode=normalized_editor_mode,
            locale=locale,
            location=target,
            graph_excerpt=self._graph_excerpt(draft, target),
            effective_configuration=self._effective_configuration(draft, target),
            validation_issues=[self._bounded_issue(item) for item in validation_issues],
            runtime_overlay=copy.deepcopy(runtime_overlay),
            evidence_refs=list(projection.evidence),
            allowed_mutations=bounded_mutations,
            extensions=bounded_extensions,
        )
        # Materialize once so the size/error contract is enforced at creation.
        envelope.canonical_bytes()
        return envelope

    def assemble_prompt(
        self,
        context: EditorContextEnvelope | dict[str, Any],
        *,
        question_text: str = "",
        budget_profile: Literal["selected", "conversation"] | None = None,
        evidence_override: Iterable[EvidenceRef | dict[str, Any]] | None = None,
    ) -> VisualProcessPromptAssembly:
        """Assemble a transient prompt bound to an immutable context snapshot.

        ``evidence_override`` is intentionally a transient input.  It allows the
        Hub to keep source text out of the persisted context while still binding
        the generated prompt to that reference-only context id.
        """

        envelope = (
            context if isinstance(context, EditorContextEnvelope) else EditorContextEnvelope.model_validate(context)
        )
        profile = budget_profile or self._context_budget_profile(envelope)
        budget = self.context_budget(profile)
        projection = self.project_evidence(
            envelope.evidence_refs if evidence_override is None else evidence_override,
            budget_profile=budget.profile,
        )
        approved = [item for item in projection.evidence if item.verification_status == VerificationStatus.verified]
        rejected = [item for item in projection.evidence if item.verification_status != VerificationStatus.verified]
        rejection_reasons = {reason for item in rejected for reason in item.reason_codes} | (
            {"evidence_not_verified"} if rejected else set()
        )
        rejection_reasons.update(projection.reason_counts)
        prior_discarded_count, prior_reasons = self._prior_budget_rejections(envelope)
        rejection_reasons.update(prior_reasons)
        rejected_count = len(rejected) + projection.discarded_count + prior_discarded_count
        response_contract = {
            "contract_version": "ananta.visual_process.help_response.v1",
            "workflow_patch_version": "ananta.visual_process.workflow_patch.v1",
            "claim_evidence_required": True,
            "automatic_mutation_forbidden": True,
            "user_question": str(question_text).strip()[:8000],
        }
        sections, prompt_text = self._render_prompt(
            envelope=envelope,
            approved=approved,
            rejected_count=rejected_count,
            rejection_reasons=rejection_reasons,
            response_contract=response_contract,
        )
        while approved and self.estimate_prompt_tokens(prompt_text) > budget.max_prompt_tokens:
            approved.pop()
            rejected_count += 1
            rejection_reasons.add("prompt_token_budget_exceeded")
            sections, prompt_text = self._render_prompt(
                envelope=envelope,
                approved=approved,
                rejected_count=rejected_count,
                rejection_reasons=rejection_reasons,
                response_contract=response_contract,
            )
        estimated_tokens = self.estimate_prompt_tokens(prompt_text)
        if estimated_tokens > budget.max_prompt_tokens:
            raise ValueError("assistant_prompt_token_budget_exceeded")
        context_id = envelope.context_id()
        evidence_ids = [item.evidence_id for item in approved]
        snapshot = self._prompt_snapshots.build_final_prompt_record(
            prompt_template_ref=PROMPT_TEMPLATE_REF,
            variables_payload={
                "context_id": context_id,
                "prompt_version": PROMPT_VERSION,
                "sections": [name for name, _ in sections],
            },
            final_prompt_text=prompt_text,
            context_hash=context_id,
            input_usage_refs=evidence_ids,
            output_schema_ref="schemas/visual_process/help_response.v1.json",
            store_raw_prompt=False,
        )
        snapshot.update(
            {
                "context_id": context_id,
                "prompt_version": PROMPT_VERSION,
                "evidence_refs": evidence_ids,
                "estimated_prompt_tokens": estimated_tokens,
                "max_prompt_tokens": budget.max_prompt_tokens,
            }
        )
        return VisualProcessPromptAssembly(
            context_id=context_id,
            prompt_version=PROMPT_VERSION,
            prompt_text=prompt_text,
            prompt_hash=hashlib.sha256(prompt_text.encode("utf-8")).hexdigest(),
            prompt_snapshot=snapshot,
            approved_evidence_refs=tuple(evidence_ids),
            rejected_evidence_count=rejected_count,
            rejection_reasons=tuple(sorted(rejection_reasons)),
            estimated_prompt_tokens=estimated_tokens,
            max_prompt_tokens=budget.max_prompt_tokens,
        )

    def with_projected_evidence(
        self,
        context: EditorContextEnvelope | dict[str, Any],
        evidence_refs: Iterable[EvidenceRef | dict[str, Any]],
        *,
        budget_profile: Literal["selected", "conversation"] = "conversation",
    ) -> EditorContextEnvelope:
        """Return an immutable context copy with audited evidence caps applied."""

        envelope = (
            context if isinstance(context, EditorContextEnvelope) else EditorContextEnvelope.model_validate(context)
        )
        budget = self.context_budget(budget_profile)
        projection = self.project_evidence(
            evidence_refs,
            budget_profile=budget.profile,
        )
        extensions = copy.deepcopy(envelope.extensions)
        extensions["ananta.context_budget"] = projection.audit_payload(budget)
        projected = envelope.model_copy(
            update={
                "evidence_refs": list(projection.evidence),
                "extensions": extensions,
            }
        )
        projected.canonical_bytes()
        return projected

    @staticmethod
    def context_budget(
        profile: Literal["selected", "conversation"] | str,
    ) -> VisualProcessContextBudget:
        normalized = str(profile or "").strip().lower()
        budget = _CONTEXT_BUDGETS.get(normalized)
        if budget is None:
            raise ValueError("visual_process_context_budget_profile_invalid")
        return budget

    @classmethod
    def project_evidence(
        cls,
        evidence_refs: Iterable[EvidenceRef | dict[str, Any]],
        *,
        budget_profile: Literal["selected", "conversation"] = "conversation",
    ) -> EvidenceBudgetProjection:
        """Apply range, line and item caps without consulting mutable state."""

        budget = cls.context_budget(budget_profile)
        refs = [item if isinstance(item, EvidenceRef) else EvidenceRef.model_validate(item) for item in evidence_refs]
        refs.sort(key=cls._evidence_sort_key)
        selected: list[EvidenceRef] = []
        seen_evidence_ids: set[str] = set()
        ranges = 0
        discarded: Counter[str] = Counter()
        truncated = 0
        for ref in refs:
            if ref.evidence_id in seen_evidence_ids:
                discarded["duplicate_evidence"] += 1
                continue
            seen_evidence_ids.add(ref.evidence_id)
            if len(selected) >= budget.max_evidence_items:
                discarded["evidence_item_budget_exceeded"] += 1
                continue
            bounded = ref
            if ref.line_start is not None:
                if ranges >= budget.max_ranges:
                    discarded["range_budget_exceeded"] += 1
                    continue
                ranges += 1
                updates: dict[str, Any] = {}
                if ref.line_end is not None and ref.line_end - ref.line_start + 1 > budget.max_lines_per_range:
                    updates["line_end"] = ref.line_start + budget.max_lines_per_range - 1
                excerpt_lines = ref.excerpt.splitlines(keepends=True) if ref.excerpt else []
                if len(excerpt_lines) > budget.max_lines_per_range:
                    updates["excerpt"] = "".join(excerpt_lines[: budget.max_lines_per_range])
                if updates:
                    bounded = ref.model_copy(update=updates)
                    truncated += 1
            selected.append(bounded)
        return EvidenceBudgetProjection(
            evidence=tuple(selected),
            discarded_count=sum(discarded.values()),
            reason_counts=dict(sorted(discarded.items())),
            truncated_range_count=truncated,
        )

    @staticmethod
    def estimate_prompt_tokens(prompt_text: str) -> int:
        """Use the repository-wide deterministic four-characters estimate."""

        return max(1, math.ceil(len(str(prompt_text or "")) / 4))

    @classmethod
    def _render_prompt(
        cls,
        *,
        envelope: EditorContextEnvelope,
        approved: list[EvidenceRef],
        rejected_count: int,
        rejection_reasons: set[str],
        response_contract: dict[str, Any],
    ) -> tuple[list[tuple[str, Any]], str]:
        sections: list[tuple[str, Any]] = [
            (
                "system_constraints",
                {
                    "hub_controls_policy_and_tasks": True,
                    "worker_must_not_orchestrate": True,
                    "source_identifiers_must_be_supplied_and_verified": True,
                    "inline_secrets_forbidden": True,
                },
            ),
            ("editor_location", envelope.location.model_dump()),
            (
                "workflow_summary",
                {
                    "graph_id": envelope.graph_id,
                    "definition_revision": envelope.definition_revision,
                    "definition_hash": envelope.definition_hash,
                    "draft_hash": envelope.draft_hash,
                    "graph_excerpt": envelope.graph_excerpt,
                },
            ),
            ("focused_entity", cls._focused_entity(envelope)),
            ("effective_configuration", envelope.effective_configuration),
            (
                "validation_and_runtime",
                {
                    "validation_issues": envelope.validation_issues,
                    "runtime_snapshot_hash": envelope.runtime_snapshot_hash,
                    "runtime_overlay": envelope.runtime_overlay,
                },
            ),
            ("allowed_mutations", envelope.allowed_mutations),
            (
                "approved_evidence",
                [
                    {
                        "evidence_id": item.evidence_id,
                        "source_id": item.source_id,
                        "source_version": item.source_version,
                        "path": item.path,
                        "line_start": item.line_start,
                        "line_end": item.line_end,
                        "trust_level": item.trust_level.value,
                        "verification_status": item.verification_status.value,
                        "excerpt": item.excerpt,
                    }
                    for item in approved
                ],
            ),
            (
                "rejected_evidence_summary",
                {
                    "count": rejected_count,
                    "reason_codes": sorted(rejection_reasons),
                },
            ),
            ("response_contract", response_contract),
        ]
        return sections, "\n".join(
            f"## {name}\n{canonical_context_bytes(value).decode('utf-8')}" for name, value in sections
        )

    @staticmethod
    def _evidence_sort_key(
        item: EvidenceRef,
    ) -> tuple[str, str, str, int, int, str]:
        return (
            str(item.source_id or ""),
            str(item.source_version or ""),
            str(item.path or ""),
            int(item.line_start or 0),
            int(item.line_end or 0),
            item.evidence_id,
        )

    @staticmethod
    def _context_budget_profile(
        envelope: EditorContextEnvelope,
    ) -> Literal["selected", "conversation"]:
        raw = envelope.extensions.get("ananta.context_budget")
        profile = str(raw.get("profile") or "") if isinstance(raw, dict) else ""
        return "selected" if profile == "selected" else "conversation"

    @staticmethod
    def _prior_budget_rejections(
        envelope: EditorContextEnvelope,
    ) -> tuple[int, set[str]]:
        audit = envelope.extensions.get("ananta.context_budget")
        if not isinstance(audit, dict):
            return 0, set()
        raw_count = audit.get("discarded_count")
        count = (
            int(raw_count) if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count > 0 else 0
        )
        raw_reasons = audit.get("discarded_reason_counts")
        reasons = (
            {
                str(reason)
                for reason, value in raw_reasons.items()
                if isinstance(value, int) and not isinstance(value, bool) and value > 0
            }
            if isinstance(raw_reasons, dict)
            else set()
        )
        return count, reasons

    @staticmethod
    def _graph_excerpt(graph: VisualProcessGraph, location: AssistantLocation) -> dict[str, Any]:
        payload = graph.definition_payload(public=True)
        steps = list(payload.get("steps") or [])
        edges = list(payload.get("edges") or [])
        if location.entity_id and location.target_kind in {"node", "field", "runtime", "validation"}:
            selected_ids = {location.entity_id}
            for edge in edges:
                if edge.get("source") == location.entity_id or edge.get("target") == location.entity_id:
                    selected_ids.update((str(edge.get("source") or ""), str(edge.get("target") or "")))
            steps = [item for item in steps if str(item.get("id") or "") in selected_ids]
            edges = [
                item
                for item in edges
                if str(item.get("source") or "") in selected_ids and str(item.get("target") or "") in selected_ids
            ]
        elif location.entity_id and location.target_kind == "edge":
            edges = [item for item in edges if str(item.get("id") or "") == location.entity_id]
            selected_ids = {str(item.get("source") or "") for item in edges} | {
                str(item.get("target") or "") for item in edges
            }
            steps = [item for item in steps if str(item.get("id") or "") in selected_ids]
        return {
            "graph_id": graph.id,
            "name": graph.name,
            "description": graph.description,
            "steps": steps[:MAX_GRAPH_EXCERPT_STEPS],
            "edges": edges[:MAX_GRAPH_EXCERPT_EDGES],
            "total_step_count": len(graph.steps),
            "total_edge_count": len(graph.edges),
            "excerpt_truncated": len(steps) > MAX_GRAPH_EXCERPT_STEPS or len(edges) > MAX_GRAPH_EXCERPT_EDGES,
        }

    @staticmethod
    def _effective_configuration(graph: VisualProcessGraph, location: AssistantLocation) -> dict[str, Any]:
        graph_metadata = copy.deepcopy(graph.metadata)
        graph_metadata.pop("owner_principal", None)
        result: dict[str, Any] = {"graph_metadata": graph_metadata}
        if location.entity_id and location.target_kind in {"node", "field", "runtime", "validation"}:
            step = graph.step_by_id(location.entity_id)
            if step is not None:
                result.update(
                    {
                        "step_kind": step.kind,
                        "step_metadata": copy.deepcopy(step.metadata),
                        "gate": step.gate,
                        "policy_hints": list(step.policy_hints),
                    }
                )
        return result

    @staticmethod
    def _focused_entity(context: EditorContextEnvelope) -> dict[str, Any]:
        target = context.location
        if target.target_kind == "edge":
            return next(
                (item for item in context.graph_excerpt.get("edges", []) if item.get("id") == target.entity_id),
                {},
            )
        return next(
            (item for item in context.graph_excerpt.get("steps", []) if item.get("id") == target.entity_id),
            {},
        )

    @staticmethod
    def _bounded_issue(raw: dict[str, Any]) -> dict[str, Any]:
        item = dict(raw or {})
        return {
            "code": str(item.get("code") or "unknown")[:160],
            "severity": str(item.get("severity") or "warning")[:40],
            "message": str(item.get("message") or "")[:1000],
            "path": str(item.get("path") or item.get("field_path") or "")[:500],
            "step_id": str(item.get("step_id") or "")[:200] or None,
            "edge_id": str(item.get("edge_id") or "")[:200] or None,
        }


visual_process_context_service = VisualProcessContextService()


__all__ = [
    "CONVERSATION_CONTEXT_BUDGET",
    "EvidenceBudgetProjection",
    "PROMPT_TEMPLATE_REF",
    "PROMPT_VERSION",
    "SELECTED_CONTEXT_BUDGET",
    "VisualProcessContextBudget",
    "VisualProcessContextService",
    "VisualProcessPromptAssembly",
    "visual_process_context_service",
]
