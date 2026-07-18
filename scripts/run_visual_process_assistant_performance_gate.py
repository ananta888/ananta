#!/usr/bin/env python3
"""Run deterministic local Visual Process Assistant operational probes.

The runner measures only in-process, network-free paths and writes no runtime
identifiers.  It emits a revision-bound evidence projection and lets the
fail-closed gate aggregator decide whether rollout is allowed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.services.visual_process_context_service import (  # noqa: E402
    CONVERSATION_CONTEXT_BUDGET,
    SELECTED_CONTEXT_BUDGET,
    VisualProcessContextService,
)
from agent.services.visual_process_location_service import VisualProcessLocationService  # noqa: E402
from agent.visual_process.models import (  # noqa: E402
    TransitionCondition,
    VisualProcessEdge,
    VisualProcessGraph,
    VisualProcessStep,
)
from ananta_contracts.retrieval import RetrievalRequest  # noqa: E402
from ananta_contracts.visual_process_assistant import EvidenceRef  # noqa: E402
from scripts.generate_visual_process_assistant_gates import (  # noqa: E402
    FRONTEND_PERFORMANCE_EVIDENCE_INPUT,
    FRONTEND_PERFORMANCE_EVIDENCE_SCHEMA,
    FRONTEND_PERFORMANCE_SOURCE_PROJECTION,
    PERFORMANCE_EVIDENCE_SCHEMA,
    PERFORMANCE_OUTPUT,
    build_performance_report,
    canonical_bytes,
    performance_source_revision,
)
from worker.retrieval.codecompass_channel_providers import JsonlSymbolProvider  # noqa: E402
from worker.retrieval.codecompass_retriever import CodeCompassRetriever  # noqa: E402

DEFAULT_EVIDENCE_OUTPUT = ROOT / "artifacts/test-gates/visual-process-assistant-performance-evidence.json"
PROBE_TEST_PATH = "tests/benchmarks/visual_process_assistant/test_operational_budgets.py"


def nearest_rank_percentile(values: Iterable[float], percentile: int) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("visual_process_performance_samples_required")
    rank = max(1, (len(ordered) * int(percentile) + 99) // 100)
    return ordered[min(rank, len(ordered)) - 1]


def _summary(samples_ms: Sequence[float]) -> dict[str, float]:
    return {
        "p50_ms": round(nearest_rank_percentile(samples_ms, 50), 6),
        "p95_ms": round(nearest_rank_percentile(samples_ms, 95), 6),
        "max_ms": round(max(samples_ms), 6),
    }


def _reference_graph(*, step_count: int, edge_count: int, graph_id: str) -> VisualProcessGraph:
    if step_count < 2 or edge_count < step_count - 1:
        raise ValueError("visual_process_reference_graph_invalid")
    steps = [
        VisualProcessStep(id=f"step-{index:04d}", label=f"Step {index}", kind="analysis") for index in range(step_count)
    ]
    pairs = [(index, index + 1) for index in range(step_count - 1)]
    pairs.extend((index % (step_count - 2), index % (step_count - 2) + 2) for index in range(edge_count - len(pairs)))
    edges = [
        VisualProcessEdge(
            id=f"edge-{index:04d}",
            source=f"step-{source:04d}",
            target=f"step-{target:04d}",
            condition=TransitionCondition(kind="always"),
        )
        for index, (source, target) in enumerate(pairs)
    ]
    return VisualProcessGraph(
        id=graph_id,
        name="Performance reference graph",
        base_graph_hash="a" * 64,
        steps=steps,
        edges=edges,
    )


def _measure_location_graph(
    graph: VisualProcessGraph,
    *,
    warmup_iterations: int,
    repetitions: int,
) -> tuple[list[float], dict[str, Any]]:
    service = VisualProcessLocationService()
    target_ids = [f"step-{(index * 37) % len(graph.steps):04d}" for index in range(repetitions)]
    for index in range(warmup_iterations):
        service.analyze(
            graph=graph,
            location={
                "target_kind": "node",
                "graph_id": graph.id,
                "entity_id": target_ids[index % len(target_ids)],
            },
        )
    samples: list[float] = []
    last: dict[str, Any] = {}
    for target_id in target_ids:
        started = time.perf_counter_ns()
        last = service.analyze(
            graph=graph,
            location={
                "target_kind": "node",
                "graph_id": graph.id,
                "entity_id": target_id,
            },
        ).as_dict()
        samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return samples, last


def run_hover_topology_probe(*, warmup_iterations: int = 10, repetitions: int = 100) -> dict[str, Any]:
    reference = _reference_graph(step_count=500, edge_count=1000, graph_id="hover-reference")
    stress = _reference_graph(step_count=1000, edge_count=2000, graph_id="topology-stress")
    reference_samples, reference_result = _measure_location_graph(
        reference,
        warmup_iterations=warmup_iterations,
        repetitions=repetitions,
    )
    stress_samples, stress_result = _measure_location_graph(
        stress,
        warmup_iterations=warmup_iterations,
        repetitions=repetitions,
    )
    reference_summary = _summary(reference_samples)
    stress_summary = _summary(stress_samples)
    return {
        "steps": 500,
        "edges": 1000,
        "repetitions": repetitions,
        "warmup_iterations": warmup_iterations,
        "delay_ms": 350,
        "p50_ms": max(reference_summary["p50_ms"], stress_summary["p50_ms"]),
        "p95_ms": max(reference_summary["p95_ms"], stress_summary["p95_ms"]),
        "max_ms": max(reference_summary["max_ms"], stress_summary["max_ms"]),
        "reference_p95_ms": reference_summary["p95_ms"],
        "stress_steps": 1000,
        "stress_edges": 2000,
        "stress_p95_ms": stress_summary["p95_ms"],
        "retrieval_requests": 0,
        "llm_requests": 0,
        "reference_result_step_count": reference_result["graph_facts"]["step_count"],
        "stress_result_step_count": stress_result["graph_facts"]["step_count"],
    }


def _symbol_records() -> list[dict[str, Any]]:
    return [
        {
            "id": f"symbol-{index:04d}",
            "kind": "python_function",
            "name": f"VisualProcessBudgetHandler{index:04d}",
            "file": f"agent/visual_process/budget_{index % 32:02d}.py",
            "summary": f"deterministic visual process budget topology handler {index:04d}",
            "content_hash": hashlib.sha256(f"record-{index}".encode("utf-8")).hexdigest(),
            "line_start": index * 3 + 1,
            "line_end": index * 3 + 3,
        }
        for index in range(512)
    ]


def run_codecompass_retrieval_probe(
    *,
    warmup_iterations: int = 10,
    repetitions: int = 100,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ananta-vpa-perf-") as directory:
        details = Path(directory) / "symbols.jsonl"
        details.write_text(
            "".join(json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n" for item in _symbol_records()),
            encoding="utf-8",
        )
        retriever = CodeCompassRetriever(
            scope="visual_process_assistant_performance",
            channel_providers={"symbol": JsonlSymbolProvider(paths=[details])},
        )
        request = RetrievalRequest(
            query="VisualProcessBudgetHandler topology budget",
            tenant_id="performance-fixture",
            scope="visual_process_assistant_performance",
            allowed_source_ids=frozenset(),
            max_results=8,
        )
        for _ in range(warmup_iterations):
            retriever.retrieve(request)
        samples: list[float] = []
        last = None
        for _ in range(repetitions):
            started = time.perf_counter_ns()
            last = retriever.retrieve(request)
            samples.append((time.perf_counter_ns() - started) / 1_000_000.0)
    assert last is not None
    summary = _summary(samples)
    diagnostics = dict(last.metadata.get("channel_diagnostics") or {})
    search_candidate_count = sum(
        int(row.get("candidate_count") or 0) for row in diagnostics.values() if isinstance(row, dict)
    )
    return {
        **summary,
        "warmup_iterations": warmup_iterations,
        "repetitions": repetitions,
        "fixture_record_count": 512,
        "hard_timeout_ms": 5000,
        "released_source_count": len(last.sources),
        "search_candidate_count": search_candidate_count,
        "rejected_count": last.rejected_count,
        "ungrounded_fixture_release_blocked": len(last.sources) == 0,
    }


def _budget_evidence() -> list[EvidenceRef]:
    common = {
        "trust_level": "inferred",
        "verification_status": "failed",
        "reason_codes": ["source_authority_unavailable"],
    }
    ranged = [
        EvidenceRef(
            evidence_id=f"budget-range-{index:02d}",
            path=f"agent/range_{index:02d}.py",
            line_start=index * 500 + 1,
            line_end=index * 500 + 500,
            **common,
        )
        for index in range(10)
    ]
    unbounded = [
        EvidenceRef(
            evidence_id=f"budget-item-{index:02d}",
            path=f"agent/z_item_{index:02d}.py",
            **common,
        )
        for index in range(10)
    ]
    return ranged + unbounded


def _context_graph() -> VisualProcessGraph:
    return VisualProcessGraph(
        id="context-budget-graph",
        name="Context budget",
        base_graph_hash="b" * 64,
        steps=[VisualProcessStep(id="step-1", label="Budget", kind="review")],
    )


def _context_measurements(profile: str) -> dict[str, Any]:
    service = VisualProcessContextService()
    context = service.build_context(
        graph=_context_graph(),
        location={
            "target_kind": "node",
            "graph_id": "context-budget-graph",
            "entity_id": "step-1",
        },
        editor_mode="editor",
        repository_revision="revision-1",
        codecompass_manifest_hash="manifest-1",
        source_allowlist_version="allowlist-1",
        evidence_refs=_budget_evidence(),
        budget_profile=profile,
    )
    assembly = service.assemble_prompt(
        context,
        question_text="Erkläre den gebundenen Kontext.",
        budget_profile=profile,
    )
    budget_audit = dict(context.extensions["ananta.context_budget"])
    ranges = [item for item in context.evidence_refs if item.line_start is not None]
    line_counts = [int(item.line_end or item.line_start or 0) - int(item.line_start or 0) + 1 for item in ranges]
    return {
        "range_count": len(ranges),
        "lines_per_range": max(line_counts, default=0),
        "prompt_tokens": assembly.estimated_prompt_tokens,
        "evidence_items": len(assembly.approved_evidence_refs),
        "projection_discarded_count": int(budget_audit["discarded_count"]),
        "prompt_rejected_count": max(
            0,
            assembly.rejected_evidence_count - int(budget_audit["discarded_count"]),
        ),
        "discarded_reason_counts": dict(budget_audit["discarded_reason_counts"]),
        "truncated_range_count": int(budget_audit["truncated_range_count"]),
        "rejection_reasons": list(assembly.rejection_reasons),
    }


def run_context_budget_probe() -> dict[str, Any]:
    selected = _context_measurements("selected")
    conversation = _context_measurements("conversation")
    oversized_graph = VisualProcessGraph(
        id="oversized-context-budget-graph",
        name="Oversized context budget",
        steps=[
            VisualProcessStep(
                id="oversized-step",
                label="Oversized",
                kind="review",
                metadata={"description": "x" * 30_000},
            )
        ],
    )
    oversized_context = VisualProcessContextService().build_context(
        graph=oversized_graph,
        location={
            "target_kind": "node",
            "graph_id": oversized_graph.id,
            "entity_id": "oversized-step",
        },
        editor_mode="editor",
        repository_revision="revision-unavailable",
        codecompass_manifest_hash="manifest-unavailable",
        source_allowlist_version="allowlist-unavailable",
        budget_profile="selected",
    )
    oversized_prompt_blocked = False
    try:
        VisualProcessContextService().assemble_prompt(
            oversized_context,
            question_text="Tokenbudget prüfen",
            budget_profile="selected",
        )
    except ValueError as exc:
        if str(exc) != "assistant_prompt_token_budget_exceeded":
            raise
        oversized_prompt_blocked = True
    return {
        "selected_ranges": selected["range_count"],
        "selected_lines_per_range": selected["lines_per_range"],
        "selected_prompt_tokens": selected["prompt_tokens"],
        "selected_evidence_items": selected["evidence_items"],
        "conversation_ranges": conversation["range_count"],
        "conversation_lines_per_range": conversation["lines_per_range"],
        "conversation_prompt_tokens": conversation["prompt_tokens"],
        "conversation_evidence_items": conversation["evidence_items"],
        "rejected_overflow_count": (
            selected["projection_discarded_count"]
            + selected["prompt_rejected_count"]
            + conversation["projection_discarded_count"]
            + conversation["prompt_rejected_count"]
        ),
        "selected_discarded_reason_counts": selected["discarded_reason_counts"],
        "conversation_discarded_reason_counts": conversation["discarded_reason_counts"],
        "selected_rejection_reasons": selected["rejection_reasons"],
        "conversation_rejection_reasons": conversation["rejection_reasons"],
        "token_budget_rejection_count": int(oversized_prompt_blocked),
        "oversized_prompt_blocked": oversized_prompt_blocked,
        "selected_limits": {
            "ranges": SELECTED_CONTEXT_BUDGET.max_ranges,
            "lines_per_range": SELECTED_CONTEXT_BUDGET.max_lines_per_range,
            "prompt_tokens": SELECTED_CONTEXT_BUDGET.max_prompt_tokens,
        },
        "conversation_limits": {
            "ranges": CONVERSATION_CONTEXT_BUDGET.max_ranges,
            "lines_per_range": CONVERSATION_CONTEXT_BUDGET.max_lines_per_range,
            "prompt_tokens": CONVERSATION_CONTEXT_BUDGET.max_prompt_tokens,
            "evidence_items": CONVERSATION_CONTEXT_BUDGET.max_evidence_items,
        },
    }


def load_frontend_performance_evidence(path: Path) -> dict[str, Any]:
    """Validate browser evidence against every covered frontend source file."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != FRONTEND_PERFORMANCE_EVIDENCE_SCHEMA:
        raise ValueError("visual_process_frontend_performance_evidence_schema_invalid")
    source_hashes = payload.get("source_hashes")
    if not isinstance(source_hashes, dict) or set(source_hashes) != set(FRONTEND_PERFORMANCE_SOURCE_PROJECTION):
        raise ValueError("visual_process_frontend_performance_source_projection_invalid")
    for relative in FRONTEND_PERFORMANCE_SOURCE_PROJECTION:
        expected = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if source_hashes.get(relative) != expected:
            raise ValueError(f"visual_process_frontend_performance_source_stale:{relative}")
    environment = payload.get("environment")
    measurements = payload.get("measurements")
    evidence_paths = payload.get("evidence_paths")
    if not isinstance(environment, dict) or not isinstance(measurements, dict):
        raise ValueError("visual_process_frontend_performance_evidence_invalid")
    if not isinstance(evidence_paths, list) or not evidence_paths:
        raise ValueError("visual_process_frontend_performance_paths_missing")
    return payload


def build_performance_evidence(
    frontend_evidence_path: Path = FRONTEND_PERFORMANCE_EVIDENCE_INPUT,
) -> dict[str, Any]:
    hover = run_hover_topology_probe()
    retrieval = run_codecompass_retrieval_probe()
    context = run_context_budget_probe()
    frontend = load_frontend_performance_evidence(frontend_evidence_path)
    frontend_environment = dict(frontend["environment"])
    return {
        "schema": PERFORMANCE_EVIDENCE_SCHEMA,
        "source_revision": performance_source_revision(),
        "environment": {
            "browser": frontend_environment["browser"],
            "build": frontend_environment["build"],
            "hardware_class": frontend_environment["hardware_class"],
            "warmup_iterations": frontend_environment["warmup_iterations"],
            "repetitions": frontend_environment["repetitions"],
        },
        "results": {
            "hover_reference_graph": {
                "measurements": hover,
                "evidence_paths": [PROBE_TEST_PATH],
            },
            "codecompass_warm_retrieval": {
                "measurements": retrieval,
                "evidence_paths": [
                    PROBE_TEST_PATH,
                    "tests/test_visual_process_assistant_operations.py",
                ],
            },
            "context_budgets": {
                "measurements": context,
                "evidence_paths": [PROBE_TEST_PATH],
            },
            "frontend_focus_stability": {
                "measurements": dict(frontend["measurements"]),
                "evidence_paths": list(frontend["evidence_paths"]),
            },
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-output", type=Path, default=DEFAULT_EVIDENCE_OUTPUT)
    parser.add_argument(
        "--frontend-evidence",
        type=Path,
        default=FRONTEND_PERFORMANCE_EVIDENCE_INPUT,
    )
    parser.add_argument("--report-output", type=Path, default=PERFORMANCE_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    evidence = build_performance_evidence(arguments.frontend_evidence)
    report = build_performance_report(evidence)
    arguments.evidence_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.evidence_output.write_bytes(canonical_bytes(evidence))
    arguments.report_output.parent.mkdir(parents=True, exist_ok=True)
    arguments.report_output.write_bytes(canonical_bytes(report))
    measured = {item["gate_id"]: item["status"] for item in report["gates"]}
    print(json.dumps({"status": report["status"], "gates": measured}, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
