from __future__ import annotations

from dataclasses import replace

import pytest

from ananta_contracts.dspy_optimization import (
    OptimizationSpecV1,
    PromptProgramV1,
    canonical_digest,
    canonical_json,
    upcast_prompt_program_with_provenance,
)
from tests.dspy_optimization.helpers import program, spec


@pytest.mark.parametrize("seed", [0, 1, 7, 2**31 - 1])
def test_spec_roundtrip_property_for_boundary_seeds(seed: int) -> None:
    value = replace(spec(), seed=seed)
    assert OptimizationSpecV1.from_mapping(value.to_dict()).digest == value.digest


@pytest.mark.parametrize("instruction", ["plain", "Grüße 世界", "emoji 🧭", "line\nseparator"])
def test_prompt_program_unicode_roundtrip_and_order_are_canonical(instruction: str) -> None:
    value = program().to_dict()
    value["signatures"][0]["instructions"] = instruction
    parsed = PromptProgramV1.from_mapping(value)
    reversed_value = {key: value[key] for key in reversed(value)}
    assert PromptProgramV1.from_mapping(reversed_value).digest == parsed.digest
    assert canonical_json(parsed.to_dict()) == canonical_json(PromptProgramV1.from_mapping(parsed.to_dict()).to_dict())


@pytest.mark.parametrize("field", ["api_key", "base_url", "class_path", "file_path"])
def test_prompt_program_rejects_unsafe_nested_state_property(field: str) -> None:
    value = program().to_dict()
    value["demonstrations"] = [{"goal": "x", "constraints": [], "tasks": [], "nested": {field: "secret"}}]
    with pytest.raises(ValueError, match="unsafe_state"):
        PromptProgramV1.from_mapping(value)


def test_upcast_is_non_mutating_and_retains_original_digest() -> None:
    raw = program().to_dict()
    raw.pop("scope")
    original = canonical_digest(raw)
    parsed, provenance = upcast_prompt_program_with_provenance(raw)
    assert "scope" not in raw
    assert provenance["source_digest"] == original
    assert provenance["result_digest"] == parsed.digest
    assert provenance["transformations"] == ["add_empty_scope"]
