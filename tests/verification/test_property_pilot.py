from __future__ import annotations

import os

from hypothesis import given, settings
from hypothesis import strategies as st

from agent.services.task_dependency_policy import (
    normalize_depends_on as task_normalize_dependencies,
)
from agent.services.task_dependency_policy import (
    normalize_text,
    validate_dependency_graph,
)
from ananta_contracts.verification import canonical_digest
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
