"""Unknown/invalid resources cannot create a synthetic efficiency promotion."""

from dataclasses import replace

import pytest

from agent.services.specialist_benchmark import ResourceUsage, SpecialistBenchmarkError
from agent.services.specialist_promotion_gate import decide_promotion
from tests.test_specialist_model_factory import POLICY, evaluated_lineage, plan, result_for


def test_missing_resources_are_explicitly_unknown():
    assert set(ResourceUsage().to_mapping().values()) == {None}


@pytest.mark.parametrize(
    "field", ["train_seconds", "train_steps", "peak_vram_bytes", "peak_ram_bytes", "parameter_count", "adapter_bytes"]
)
@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "0", 2**63])
def test_invalid_resource_values_are_rejected(field, value):
    with pytest.raises(SpecialistBenchmarkError, match="resource_usage_invalid"):
        ResourceUsage(**{field: value})


@pytest.mark.parametrize("missing", [None, 0, False, -1])
def test_unknown_or_invalid_resource_does_not_promote_equal_quality(missing):
    _tiny, candidate = plan().candidates
    good = [("allow", "allow", 0.9), ("deny", "deny", 0.9)]
    previous = result_for(candidate, good, adapter_bytes=900, train_seconds=10)
    result = result_for(candidate, good, adapter_bytes=900, train_seconds=10)
    result = replace(result, resources={**result.resources, "train_seconds": missing})
    decision = decide_promotion(
        candidate=result,
        lineage=evaluated_lineage(result, "v2"),
        policy=POLICY,
        previous=previous,
        previous_version="v1",
    )
    assert not decision.allowed and "no_measurable_improvement" in decision.reason_codes


def test_nonfinite_benchmark_cannot_be_bound_to_evaluated_lineage():
    candidate = plan().candidates[1]
    result = result_for(candidate, [("allow", "allow", 0.9)], adapter_bytes=900)
    malformed = replace(result, resources={**result.resources, "train_seconds": float("inf")})
    with pytest.raises(ValueError):
        evaluated_lineage(malformed, "v2")
