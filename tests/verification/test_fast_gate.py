from hypothesis import given, settings
from hypothesis import strategies as st

from ananta_contracts.verification import canonical_digest
from worker.verification.pilot_targets import clamp, normalize_identifier, permission_subset_is_monotone


@settings(max_examples=30, derandomize=True, database=None, deadline=None)
@given(value=st.integers(), lower=st.integers(), width=st.integers(min_value=0, max_value=1000))
def test_fast_clamp(value: int, lower: int, width: int) -> None:
    upper = lower + width
    assert lower <= clamp(value, lower, upper) <= upper


@settings(max_examples=30, derandomize=True, database=None, deadline=None)
@given(st.text(max_size=20))
def test_fast_normalization(value: str) -> None:
    result = normalize_identifier(value)
    assert normalize_identifier(result) == result


@settings(max_examples=30, derandomize=True, database=None, deadline=None)
@given(st.sets(st.text(max_size=8)), st.sets(st.text(max_size=8)))
def test_fast_permission(required: set[str], granted: set[str]) -> None:
    assert permission_subset_is_monotone(required, granted) == (required <= granted)


@settings(max_examples=30, derandomize=True, database=None, deadline=None)
@given(st.dictionaries(st.text(min_size=1, max_size=6), st.integers(), max_size=8))
def test_fast_digest(value: dict[str, int]) -> None:
    assert canonical_digest(value) == canonical_digest(dict(reversed(list(value.items()))))
