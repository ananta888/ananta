"""Deterministic non-default collection target for adapter classification tests."""

from hypothesis import given
from hypothesis import strategies as st


@given(st.integers())
def test_seeded_property_violation(value: int) -> None:
    assert value != 0
