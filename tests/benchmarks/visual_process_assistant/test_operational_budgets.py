"""Reproducible operational budgets for the Visual Process Assistant."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from agent.services.visual_process_context_service import (
    CONVERSATION_CONTEXT_BUDGET,
    SELECTED_CONTEXT_BUDGET,
    VisualProcessContextService,
)
from scripts.generate_visual_process_assistant_gates import build_performance_report
from scripts.run_visual_process_assistant_performance_gate import (
    _budget_evidence,
    _context_graph,
    build_performance_evidence,
    nearest_rank_percentile,
)


@pytest.fixture(scope="module")
def measured_evidence() -> dict:
    """Run the same 100-iteration probes used to generate committed evidence."""

    return build_performance_evidence()


def _measurements(evidence: Mapping, gate_id: str) -> dict:
    return dict(evidence["results"][gate_id]["measurements"])


def test_nearest_rank_percentile_is_deterministic_and_rejects_empty_samples() -> None:
    assert nearest_rank_percentile([5, 1, 4, 2, 3], 50) == 3
    assert nearest_rank_percentile([5, 1, 4, 2, 3], 95) == 5
    with pytest.raises(ValueError, match="performance_samples_required"):
        nearest_rank_percentile([], 95)


def test_500_node_hover_and_1000_node_topology_stay_below_100ms(
    measured_evidence: dict,
) -> None:
    measurements = _measurements(measured_evidence, "hover_reference_graph")

    assert measurements["steps"] == 500
    assert measurements["edges"] == 1000
    assert measurements["stress_steps"] == 1000
    assert measurements["stress_edges"] == 2000
    assert measurements["repetitions"] == 100
    assert measurements["p95_ms"] <= 100
    assert measurements["reference_p95_ms"] <= 100
    assert measurements["stress_p95_ms"] <= 100
    assert measurements["retrieval_requests"] == 0
    assert measurements["llm_requests"] == 0


def test_real_codecompass_finds_candidates_but_fails_closed_without_authority_below_two_seconds(
    measured_evidence: dict,
) -> None:
    measurements = _measurements(measured_evidence, "codecompass_warm_retrieval")

    assert measurements["fixture_record_count"] == 512
    assert measurements["repetitions"] == 100
    assert measurements["p95_ms"] <= 2_000
    assert measurements["hard_timeout_ms"] == 5_000
    assert measurements["released_source_count"] == 0
    assert measurements["search_candidate_count"] >= 1
    assert measurements["rejected_count"] > 0
    assert measurements["ungrounded_fixture_release_blocked"] is True


def test_selected_and_conversation_contexts_enforce_all_caps(
    measured_evidence: dict,
) -> None:
    measurements = _measurements(measured_evidence, "context_budgets")

    assert measurements["selected_ranges"] <= SELECTED_CONTEXT_BUDGET.max_ranges
    assert measurements["selected_lines_per_range"] <= SELECTED_CONTEXT_BUDGET.max_lines_per_range
    assert measurements["selected_prompt_tokens"] <= SELECTED_CONTEXT_BUDGET.max_prompt_tokens
    assert measurements["selected_evidence_items"] <= SELECTED_CONTEXT_BUDGET.max_evidence_items
    assert measurements["conversation_ranges"] <= CONVERSATION_CONTEXT_BUDGET.max_ranges
    assert measurements["conversation_lines_per_range"] <= CONVERSATION_CONTEXT_BUDGET.max_lines_per_range
    assert measurements["conversation_prompt_tokens"] <= CONVERSATION_CONTEXT_BUDGET.max_prompt_tokens
    assert measurements["conversation_evidence_items"] <= CONVERSATION_CONTEXT_BUDGET.max_evidence_items
    assert measurements["rejected_overflow_count"] > 0
    assert measurements["conversation_discarded_reason_counts"]["range_budget_exceeded"] > 0
    assert measurements["token_budget_rejection_count"] == 1
    assert measurements["oversized_prompt_blocked"] is True


def test_context_projection_is_order_independent_and_auditable() -> None:
    service = VisualProcessContextService()
    common = {
        "graph": _context_graph(),
        "location": {
            "target_kind": "node",
            "graph_id": "context-budget-graph",
            "entity_id": "step-1",
        },
        "editor_mode": "editor",
        "repository_revision": "revision-1",
        "codecompass_manifest_hash": "manifest-1",
        "source_allowlist_version": "allowlist-1",
        "budget_profile": "conversation",
    }
    evidence = _budget_evidence()

    forward = service.build_context(evidence_refs=evidence, **common)
    reverse = service.build_context(evidence_refs=reversed(evidence), **common)
    assembly = service.assemble_prompt(forward, question_text="Budget prüfen")

    assert forward.context_id() == reverse.context_id()
    assert forward.canonical_bytes() == reverse.canonical_bytes()
    assert len(forward.evidence_refs) == CONVERSATION_CONTEXT_BUDGET.max_evidence_items
    audit = forward.extensions["ananta.context_budget"]
    assert audit["discarded_count"] > 0
    assert audit["discarded_reason_counts"]["range_budget_exceeded"] > 0
    assert assembly.estimated_prompt_tokens <= assembly.max_prompt_tokens
    assert assembly.rejected_evidence_count > 0
    assert "range_budget_exceeded" in assembly.rejection_reasons
    assert "source_authority_unavailable" in assembly.rejection_reasons


def test_range_projection_truncates_declared_lines_without_authority_content() -> None:
    raw = _budget_evidence()[0].model_dump(mode="json")
    projection = VisualProcessContextService.project_evidence(
        [raw],
        budget_profile="selected",
    )

    assert len(projection.evidence) == 1
    bounded = projection.evidence[0]
    assert bounded.line_start is not None
    assert bounded.line_end == bounded.line_start + 79
    assert bounded.excerpt is None
    assert projection.truncated_range_count == 1
    assert projection.audit_payload(SELECTED_CONTEXT_BUDGET)["truncation_reason_counts"] == {
        "range_line_budget_truncated": 1
    }


def test_projection_deduplicates_evidence_ids_before_prompting() -> None:
    first = _budget_evidence()[0].model_dump(mode="json")
    duplicate = {
        **first,
        "path": "agent/duplicate.py",
        "line_start": 1,
        "line_end": 2,
    }

    projection = VisualProcessContextService.project_evidence(
        [duplicate, first],
        budget_profile="selected",
    )

    assert len(projection.evidence) == 1
    assert projection.discarded_count == 1
    assert projection.reason_counts == {"duplicate_evidence": 1}


def test_all_measured_performance_gates_pass(
    measured_evidence: dict,
) -> None:
    report = build_performance_report(measured_evidence)
    statuses = {item["gate_id"]: item["status"] for item in report["gates"]}

    assert statuses == {
        "hover_reference_graph": "passed",
        "codecompass_warm_retrieval": "passed",
        "context_budgets": "passed",
        "frontend_focus_stability": "passed",
    }
    assert report["status"] == "passed"
    assert report["release_allowed"] is True
    assert report["reason_codes"] == []
