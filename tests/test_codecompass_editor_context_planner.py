from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.services.codecompass_context_planner_service import (
    SCHEMA_EDITOR_CONTEXT_BUNDLE,
    CodeCompassContextPlanner,
)
from agent.services.codecompass_editor_context_contract import (
    EDITOR_QUERY_SCHEMA,
    MAX_USER_LANGUAGE_CHARS,
    CodeCompassEditorIntent,
    CodeCompassEditorQueryInput,
    intent_for_location,
)
from agent.services.visual_process_context_service import VisualProcessContextService

INTENTS = tuple(item.value for item in CodeCompassEditorIntent)


class _Retrieval:
    def __init__(self, records):
        self.records = list(records)
        self.calls: list[dict[str, object]] = []

    def search_records(self, query, *, limit, task_kind=None, retrieval_intent=None):
        self.calls.append(
            {
                "query": query,
                "limit": limit,
                "task_kind": task_kind,
                "retrieval_intent": retrieval_intent,
            }
        )
        return list(self.records)


class _ExplodingRetrieval:
    def search_records(self, *_args, **_kwargs):  # pragma: no cover - failure proves a regression
        raise AssertionError("preview must not resolve or call retrieval")


def _query(*, intent="node_explanation", detail_level="conversation", **overrides):
    payload = {
        "schema": EDITOR_QUERY_SCHEMA,
        "intent": intent,
        "detail_level": detail_level,
        "registry_version": "registry-v7",
        "node_kind": "analysis",
        "field_path": "metadata.model_routing.strategy",
        "backend_contract": {"handler": "analysis.execute", "version": 2},
        "symbols": ["AnalysisHandler", "execute"],
        "graph_neighbors": ["step-z", "step-a"],
        "user_language": "Was bewirkt diese Einstellung?",
    }
    payload.update(overrides)
    return payload


def _hit(
    record_id: str,
    *,
    path: str | None = None,
    line_start: int = 1,
    line_end: int = 20,
    score: float = 0.5,
    verification_status: str = "verified",
    trust_level: str = "deterministic",
    estimated_tokens: int = 80,
    content: str = "bounded index excerpt",
):
    return {
        "id": record_id,
        "record_id": record_id,
        "path": path or f"agent/{record_id}.py",
        "line_start": line_start,
        "line_end": line_end,
        "score": score,
        "verification_status": verification_status,
        "trust_level": trust_level,
        "estimated_tokens": estimated_tokens,
        "content": content,
        "symbol": f"symbol_{record_id}",
    }


@pytest.mark.parametrize("intent", INTENTS)
def test_all_seven_editor_intents_are_typed_and_plannable(intent: str) -> None:
    planner = CodeCompassContextPlanner(retrieval_service=_ExplodingRetrieval())

    bundle = planner.plan_editor_context(
        query_input=_query(intent=intent, detail_level="preview"),
    )

    assert bundle["schema"] == SCHEMA_EDITOR_CONTEXT_BUNDLE
    assert bundle["query_input"]["intent"] == intent
    assert bundle["query_input"]["schema"] == EDITOR_QUERY_SCHEMA


def test_query_contract_is_structural_bounded_and_canonical() -> None:
    contract = CodeCompassEditorQueryInput.from_mapping(
        _query(
            backend_contract={"z": 2, "a": 1},
            symbols=["zeta", "alpha", "zeta"],
            graph_neighbors=["step-2", "step-1", "step-2"],
            user_language="  " + ("x" * 2_000) + "  ",
        )
    )

    assert contract.backend_contract == '{"a":1,"z":2}'
    assert contract.symbols == ("alpha", "zeta")
    assert contract.graph_neighbors == ("step-1", "step-2")
    assert len(contract.user_language) == MAX_USER_LANGUAGE_CHARS
    assert contract.retrieval_query().startswith(
        "intent:node_explanation\nregistry_version:registry-v7\nnode_kind:analysis"
    )
    assert contract.retrieval_query() == CodeCompassEditorQueryInput.from_mapping(contract.as_dict()).retrieval_query()

    with pytest.raises(ValueError, match="backend_contract_or_symbols_required"):
        CodeCompassEditorQueryInput.from_mapping(_query(backend_contract=None, symbols=[]))
    with pytest.raises(ValueError, match="schema_invalid"):
        CodeCompassEditorQueryInput.from_mapping(_query(schema="unknown.v1"))
    assert intent_for_location(target_kind="field", role="input") == CodeCompassEditorIntent.io_contract


def test_preview_is_metadata_only_with_zero_operational_calls() -> None:
    planner = CodeCompassContextPlanner(retrieval_service=_ExplodingRetrieval())

    bundle = planner.plan_editor_context(
        query_input=_query(detail_level="preview"),
        include_neighbors=True,
    )

    assert bundle["location_refs"] == []
    assert bundle["patch_targets"] == []
    assert bundle["budget"] == {
        "profile": "preview",
        "max_ranges": 0,
        "max_lines_per_range": 0,
        "max_neighbors": 0,
        "max_evidence_items": 0,
        "max_tokens": 0,
    }
    assert bundle["diagnostics"]["retrieval_calls"] == 0
    assert bundle["diagnostics"]["graph_expansion_calls"] == 0
    assert bundle["diagnostics"]["repository_content_reads"] == 0
    assert bundle["diagnostics"]["llm_calls"] == 0
    assert bundle["warnings"] == ["preview_metadata_only"]


@pytest.mark.parametrize("profile", ["selected", "conversation"])
def test_editor_planner_uses_exact_operational_budgets(profile: str) -> None:
    policy = VisualProcessContextService.context_budget(profile)
    records = [
        _hit(
            f"record-{index:02d}",
            line_start=1,
            line_end=policy.max_lines_per_range + 100,
            estimated_tokens=1_000,
            score=1 - index / 100,
        )
        for index in range(20)
    ]
    planner = CodeCompassContextPlanner(retrieval_service=_Retrieval(records))

    bundle = planner.plan_editor_context(
        query_input=_query(detail_level=profile),
        include_neighbors=False,
    )

    assert bundle["budget"] == {
        "profile": profile,
        "max_ranges": policy.max_ranges,
        "max_lines_per_range": policy.max_lines_per_range,
        "max_neighbors": 4 if profile == "selected" else 6,
        "max_evidence_items": policy.max_evidence_items,
        "max_tokens": policy.max_prompt_tokens,
    }
    assert len(bundle["location_refs"]) == policy.max_ranges
    assert len(bundle["location_refs"]) <= policy.max_evidence_items
    assert all(ref["line_end"] - ref["line_start"] + 1 == policy.max_lines_per_range for ref in bundle["location_refs"])
    assert bundle["budget_usage"]["estimated_tokens"] <= policy.max_prompt_tokens
    assert bundle["budget_usage"]["ranges"] == policy.max_ranges
    assert "range_budget_exceeded" in {row["reason"] for row in bundle["discarded"]}


def test_sort_order_is_verification_trust_score_path_line_record() -> None:
    records = [
        _hit("failed", verification_status="failed", score=100),
        _hit("unverified", verification_status="unverified", score=100),
        _hit("inferred", trust_level="inferred", score=100),
        _hit("score-low", score=0.4, path="agent/a.py", line_start=5),
        _hit("record-z", score=0.9, path="agent/a.py", line_start=5),
        _hit("record-a", score=0.9, path="agent/a.py", line_start=5),
        _hit("path-first", score=0.9, path="agent/0.py", line_start=20),
        _hit("line-first", score=0.9, path="agent/a.py", line_start=1),
    ]
    planner = CodeCompassContextPlanner(retrieval_service=_Retrieval(reversed(records)))

    bundle = planner.plan_editor_context(
        query_input=_query(),
        include_neighbors=False,
    )

    assert [ref["record_id"] for ref in bundle["location_refs"]] == [
        "path-first",
        "line-first",
        "record-a",
        "record-z",
        "score-low",
        "inferred",
        "unverified",
        "failed",
    ]


def test_bundle_and_trace_are_order_independent_and_byte_stable() -> None:
    records = [
        _hit("record-c", score=0.7),
        _hit("record-a", score=0.9),
        _hit("record-b", score=0.8),
    ]
    forward = CodeCompassContextPlanner(retrieval_service=_Retrieval(records)).plan_editor_context(
        query_input=_query(), include_neighbors=False
    )
    reverse = CodeCompassContextPlanner(retrieval_service=_Retrieval(reversed(records))).plan_editor_context(
        query_input=_query(), include_neighbors=False
    )

    assert forward["bundle_id"] == reverse["bundle_id"]
    assert forward["bundle_id"].startswith("cc-editor-sha256:")
    assert len(forward["bundle_id"].removeprefix("cc-editor-sha256:")) == 64
    assert json.dumps(forward, sort_keys=True, separators=(",", ":")) == json.dumps(
        reverse,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_graph_expansion_seeds_are_selected_in_stable_rank_order() -> None:
    class _NeighborPlanner(CodeCompassContextPlanner):
        def __init__(self, records):
            super().__init__(retrieval_service=_Retrieval(records))
            self.seed_ids: list[str] = []

        def _neighbor_refs(self, seeds):
            self.seed_ids = [str(seed["record_id"]) for seed in seeds]
            return []

    records = [_hit(f"record-{index}", score=index / 10) for index in range(8)]
    forward = _NeighborPlanner(records)
    reverse = _NeighborPlanner(reversed(records))

    forward_bundle = forward.plan_editor_context(query_input=_query())
    reverse_bundle = reverse.plan_editor_context(query_input=_query())

    assert (
        forward.seed_ids
        == reverse.seed_ids
        == [
            "record-7",
            "record-6",
            "record-5",
            "record-4",
            "record-3",
            "record-2",
        ]
    )
    assert forward_bundle["bundle_id"] == reverse_bundle["bundle_id"]


def test_every_discard_is_safe_and_present_in_the_budget_trace() -> None:
    records = [
        "not-a-record",
        {"id": "missing-location", "content": "must-not-leak"},
        _hit("a", path="agent/a.py", estimated_tokens=3_000, score=1.0),
        _hit("b", path="agent/b.py", estimated_tokens=3_000, score=0.9),
        _hit("c", path="agent/c.py", estimated_tokens=100, score=0.8),
        _hit("d", path="agent/d.py", estimated_tokens=100, score=0.7),
        _hit("e", path="agent/e.py", estimated_tokens=100, score=0.6),
        _hit("f", path="agent/f.py", estimated_tokens=100, score=0.5),
        _hit("a", path="agent/z-duplicate.py", estimated_tokens=100, score=0.4),
    ]
    bundle = CodeCompassContextPlanner(retrieval_service=_Retrieval(records)).plan_editor_context(
        query_input=_query(detail_level="selected"),
        include_neighbors=False,
    )

    reasons = {row["reason"] for row in bundle["discarded"]}
    assert {
        "candidate_invalid",
        "location_range_invalid",
        "duplicate_evidence",
        "token_budget_exceeded",
        "range_budget_exceeded",
    } <= reasons
    trace_discards = [row for row in bundle["budget_trace"] if row["decision"] == "discarded"]
    assert len(trace_discards) == len(bundle["discarded"])
    assert sorted(row["reason"] for row in trace_discards) == sorted(row["reason"] for row in bundle["discarded"])
    assert all(set(row) <= {"record_id", "path", "line_start", "line_end", "reason"} for row in bundle["discarded"])
    assert "must-not-leak" not in json.dumps(bundle)


def test_full_repository_content_cannot_become_a_planner_prompt() -> None:
    marker = "FULL_REPOSITORY_CONTENT_MUST_NEVER_ESCAPE"
    retrieval = _Retrieval(
        [
            _hit(
                "huge",
                content=marker * 100_000,
                estimated_tokens=500_000,
            )
        ]
    )
    bundle = CodeCompassContextPlanner(retrieval_service=retrieval).plan_editor_context(
        query_input=_query(user_language="x" * 10_000),
        include_neighbors=False,
    )
    encoded = json.dumps(bundle, ensure_ascii=False, sort_keys=True)

    assert marker not in encoded
    assert "content" not in bundle["discarded"][0]
    assert len(bundle["query_input"]["user_language"]) == MAX_USER_LANGUAGE_CHARS
    assert len(bundle["query_text"]) < 10_000
    assert bundle["location_refs"] == []
    assert bundle["discarded"][0]["reason"] == "token_budget_exceeded"


def test_editor_context_projection_uses_snapshot_structure_only() -> None:
    context = SimpleNamespace(
        location=SimpleNamespace(
            target_kind="field",
            entity_id="step-1",
            field_path="metadata.model",
            role=None,
        ),
        node_registry_version="registry-v9",
        graph_excerpt={
            "steps": [
                {"id": "step-1", "kind": "model", "io": {"inputs": ["prompt"]}},
                {"id": "step-2", "kind": "validator"},
            ],
            "edges": [{"source": "step-1", "target": "step-2"}],
        },
        effective_configuration={"step_kind": "model", "step_metadata": {}},
        extensions={"ananta.context_budget": {"profile": "selected"}},
    )

    contract = CodeCompassEditorQueryInput.from_editor_context(
        context,
        user_language="Warum?",
    )

    assert contract.intent == CodeCompassEditorIntent.field_effect
    assert contract.detail_level.value == "selected"
    assert contract.registry_version == "registry-v9"
    assert contract.node_kind == "model"
    assert contract.field_path == "metadata.model"
    assert contract.backend_contract == '{"inputs":["prompt"]}'
    assert contract.graph_neighbors == ("step-2",)


def test_plan_context_tool_routes_typed_editor_inputs_to_the_production_planner(
    monkeypatch,
    tmp_path,
) -> None:
    retrieval = _Retrieval([_hit("typed-tool-result")])
    monkeypatch.setattr(
        "agent.services.knowledge_index_retrieval_service.get_knowledge_index_retrieval_service",
        lambda: retrieval,
    )
    from agent.services.tools.codecompass_tools import codecompass_plan_context

    result = codecompass_plan_context(
        workspace_dir=str(tmp_path),
        tool_call_id="tool_result:typed-editor",
        arguments={
            "query": "Was bewirkt das Feld?",
            "intent": "field_effect",
            "detail_level": "selected",
            "registry_version": "registry-v7",
            "node_kind": "analysis",
            "field_path": "metadata.strategy",
            "symbols": ["AnalysisHandler"],
            "graph_neighbors": ["step-2"],
            "include_neighbors": False,
        },
    )

    bundle = result["data"]["context_bundle"]
    assert result["status"] == "ok"
    assert bundle["schema"] == SCHEMA_EDITOR_CONTEXT_BUNDLE
    assert bundle["query_input"]["intent"] == "field_effect"
    assert bundle["query_input"]["detail_level"] == "selected"
    assert retrieval.calls[0]["retrieval_intent"] == "field_effect"
    assert retrieval.calls[0]["query"] == bundle["query_text"]
