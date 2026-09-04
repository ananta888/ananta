from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from agent.services.task_dependency_policy import (
    normalize_depends_on as task_normalize_dependencies,
)
from agent.services.task_dependency_policy import (
    normalize_text,
    validate_dependency_graph,
)
from agent.services.verification_policy_service import default_verification_spec, evaluate_quality_gates
from ananta_contracts.hub_evidence import build_hub_evidence_assignment, validate_hub_evidence_assignment
from ananta_contracts.verification import VerificationBudgets, canonical_digest
from worker.verification.crosshair_output import CrossHairOutputParser
from worker.verification.pilot_targets import (
    clamp,
    normalize_dependencies,
    normalize_identifier,
    permission_subset_is_monotone,
    unique_in_order,
)

_BACKEND = os.environ.get("ANANTA_HYPOTHESIS_BACKEND", "hypothesis")
_CASES = int(os.environ.get("ANANTA_HYPOTHESIS_CASES", "100"))
_SETTINGS = settings(
    backend=_BACKEND,
    max_examples=_CASES,
    deadline=None,
    database=None,
    derandomize=_BACKEND == "hypothesis",
)
_TEXT = st.text(alphabet=st.characters(categories=("L", "N", "Zs")), max_size=40)


@_SETTINGS
@given(value=st.integers(), lower=st.integers(), width=st.integers(min_value=0, max_value=10_000))
def test_clamp_stays_within_bounds(value: int, lower: int, width: int) -> None:
    upper = lower + width
    assert lower <= clamp(value, lower, upper) <= upper


@_SETTINGS
@given(_TEXT)
def test_identifier_normalization_is_idempotent(value: str) -> None:
    normalized = normalize_identifier(value)
    assert normalize_identifier(normalized) == normalized


@_SETTINGS
@given(st.lists(st.integers(), max_size=50))
def test_unique_output_contains_no_duplicates(values: list[int]) -> None:
    result = unique_in_order(values)
    assert len(result) == len(set(result))


@_SETTINGS
@given(st.lists(st.integers(), max_size=50))
def test_unique_output_preserves_first_occurrence_order(values: list[int]) -> None:
    result = unique_in_order(values)
    assert result == sorted(set(values), key=values.index)


@_SETTINGS
@given(
    required=st.sets(st.sampled_from(["read", "write", "admin"])),
    granted=st.sets(st.sampled_from(["read", "write", "admin"])),
    additions=st.sets(st.sampled_from(["read", "write", "admin"])),
)
def test_permission_allow_is_monotone(required: set[str], granted: set[str], additions: set[str]) -> None:
    if permission_subset_is_monotone(required, granted):
        assert permission_subset_is_monotone(required, granted | additions)


@_SETTINGS
@given(values=st.lists(_TEXT, max_size=30), task_id=_TEXT)
def test_dependency_normalization_removes_self_and_duplicates(values: list[str], task_id: str) -> None:
    result = normalize_dependencies(values, task_id)
    assert task_id not in result
    assert len(result) == len(set(result))


@_SETTINGS
@given(_TEXT)
def test_existing_text_normalization_is_idempotent(value: str) -> None:
    assert normalize_text(normalize_text(value)) == normalize_text(value)


@_SETTINGS
@given(values=st.lists(_TEXT, max_size=30), task_id=_TEXT)
def test_existing_dependency_normalizer_preserves_order(values: list[str], task_id: str) -> None:
    result = task_normalize_dependencies(values, tid=task_id)
    assert result == list(dict.fromkeys(item.strip() for item in values if item.strip() and item.strip() != task_id))


@_SETTINGS
@given(st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), max_size=10))
def test_contract_digest_ignores_mapping_insertion_order(value: dict[str, int]) -> None:
    assert canonical_digest(value) == canonical_digest(dict(reversed(list(value.items()))))


@_SETTINGS
@given(node=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=12))
def test_dependency_cycle_always_fails_closed(node: str) -> None:
    valid, reason = validate_dependency_graph({node: [node]})
    assert not valid
    assert reason == "dependency_cycle_detected"


@_SETTINGS
@given(_TEXT)
def test_production_text_normalization_has_canonical_spacing(value: str) -> None:
    normalized = normalize_text(value)
    assert normalized == normalized.strip()
    assert "  " not in normalized


@_SETTINGS
@given(values=st.lists(_TEXT, max_size=30), task_id=_TEXT)
def test_production_dependency_normalization_is_idempotent(values: list[str], task_id: str) -> None:
    first = task_normalize_dependencies(values, tid=task_id)
    assert task_normalize_dependencies(first, tid=task_id) == first


@_SETTINGS
@given(edges=st.sets(st.tuples(st.integers(0, 10), st.integers(0, 10)), max_size=50))
def test_forward_only_dependency_graph_is_acyclic(edges: set[tuple[int, int]]) -> None:
    graph = {str(index): [] for index in range(11)}
    for source, target in edges:
        if source < target:
            graph[str(source)].append(str(target))
    valid, reason = validate_dependency_graph(graph)
    assert (valid, reason) == (True, "")


@_SETTINGS
@given(size=st.integers(min_value=1, max_value=20))
def test_closed_dependency_chain_with_back_edge_is_rejected(size: int) -> None:
    graph = {str(index): [str((index + 1) % size)] for index in range(size)}
    valid, reason = validate_dependency_graph(graph)
    assert (valid, reason) == (False, "dependency_cycle_detected")


@_SETTINGS
@given(
    timeout=st.integers(1, 3600),
    cases=st.integers(1, 1_000_000),
    targets=st.integers(1, 100),
    output=st.integers(256, 10_000_000),
)
def test_verification_budgets_preserve_valid_closed_bounds(timeout: int, cases: int, targets: int, output: int) -> None:
    budget = VerificationBudgets(timeout, cases, targets, output)
    assert (budget.timeout_seconds, budget.max_cases, budget.max_targets, budget.max_output_bytes) == (
        timeout,
        cases,
        targets,
        output,
    )


@_SETTINGS
@given(
    field=st.sampled_from(["timeout_seconds", "max_cases", "max_targets", "max_output_bytes"]),
    invalid=st.sampled_from([True, False, 0, -1]),
)
def test_verification_budgets_reject_adversarial_values(field: str, invalid: object) -> None:
    values = {"timeout_seconds": 10, "max_cases": 10, "max_targets": 5, "max_output_bytes": 1024}
    values[field] = invalid
    with pytest.raises(ValueError, match="verification_budget_invalid"):
        VerificationBudgets(**values)


@_SETTINGS
@given(field=st.sampled_from(["run_id", "task_id", "dispatch_lease_id", "binding_digest"]))
def test_hub_evidence_projection_mutation_fails_closed(field: str) -> None:
    evidence = build_hub_evidence_assignment(
        run_id="RUN_property_test",
        task_id="task-property-test",
        assignment_id="assignment-property-test",
        dispatch_lease_id="lease-property-test",
        source_ids=["SRC_property_test"],
        evidence_scope="test",
        binding_digest="a" * 64,
    )
    mutated = dict(evidence)
    mutated[field] = "b" * 64 if field == "binding_digest" else f"mutated-{field}"
    with pytest.raises(ValueError):
        validate_hub_evidence_assignment(mutated)


@_SETTINGS
@given(exit_code=st.integers().filter(lambda value: value != 0), output=st.text(max_size=80))
def test_quality_gate_nonzero_exit_is_always_denied(exit_code: int, output: str) -> None:
    task = SimpleNamespace(title="implement feature", description="coding task")
    assert evaluate_quality_gates(task, output, exit_code) == (False, "non_zero_exit_code")


@_SETTINGS
@given(task_kind=st.one_of(st.none(), st.text(max_size=40)))
def test_default_verification_spec_is_closed_and_boolean(task_kind: str | None) -> None:
    spec = default_verification_spec({"task_kind": task_kind})
    assert set(spec) == {"lint", "tests", "policy", "mode"}
    assert all(type(spec[key]) is bool for key in ("lint", "tests", "policy"))
    assert spec["mode"] == "quality_gates"


@_SETTINGS
@given(value=st.sampled_from([float("nan"), float("inf"), float("-inf")]))
def test_contract_digest_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValueError):
        canonical_digest({"value": value})


@_SETTINGS
@given(
    value=st.recursive(
        st.one_of(st.none(), st.booleans(), st.integers(), st.text(max_size=20)),
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(st.text(min_size=1, max_size=8), children, max_size=4),
            st.tuples(children, children),
        ),
        max_leaves=12,
    )
)
def test_crosshair_parser_fuzzes_balanced_literal_arguments(value) -> None:
    parsed = CrossHairOutputParser().parse(f"false when calling module.fn(value = {value!r})")
    expected = json.loads(json.dumps(value, ensure_ascii=True))
    assert parsed[0].arguments == {"value": expected}
