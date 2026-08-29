from __future__ import annotations

import pytest

from agent.services.dspy_engine_capability_service import DspyEngineCapabilityService
from agent.services.dspy_optimization_policy import DspyOptimizationPolicy
from ananta_contracts.dspy_optimization import DatasetManifestV1, OptimizationRunState, PromptProgramV1
from tests.dspy_optimization.helpers import DIGEST, program, spec


def test_spec_is_closed_digest_stable_and_provider_bound() -> None:
    value = spec()
    assert value.digest == spec().digest
    with pytest.raises(ValueError, match="unknown_field"):
        type(value).from_mapping({**value.to_dict(), "base_url": "https://unsafe.example"})


def test_run_state_machine_rejects_stale_or_terminal_transitions() -> None:
    OptimizationRunState.ADMITTED.assert_transition(OptimizationRunState.RUNNING)
    with pytest.raises(ValueError, match="transition_invalid"):
        OptimizationRunState.COMPLETED.assert_transition(OptimizationRunState.RUNNING)


def test_dataset_splits_are_disjoint_and_source_ids_are_never_invented() -> None:
    manifest = DatasetManifestV1(
        tenant_id="tenant-1",
        dataset_id="dataset-1",
        version=1,
        content_digest=DIGEST,
        record_schema_digest="b" * 64,
        split_digests={"train": "c" * 64, "validation": "d" * 64, "test": "e" * 64},
        split_record_ids={"train": ["one"], "validation": ["two"], "test": ["three"]},
        license_id="internal",
        sensitivity="internal",
        retention_days=30,
        source_refs=[],
    )
    assert len(manifest.digest) == 64
    with pytest.raises(ValueError, match="split_leakage"):
        DatasetManifestV1(
            tenant_id="tenant-1",
            dataset_id="dataset-1",
            version=1,
            content_digest=DIGEST,
            record_schema_digest="b" * 64,
            split_digests={"train": "c" * 64, "validation": "d" * 64, "test": "e" * 64},
            split_record_ids={"train": ["one"], "validation": ["one"], "test": ["three"]},
            license_id="internal",
            sensitivity="internal",
            retention_days=30,
        )


def test_prompt_program_rejects_provider_and_executable_state() -> None:
    raw = program().to_dict()
    raw["demonstrations"] = [{"api_key": "secret"}]
    with pytest.raises(ValueError, match="unsafe_state"):
        PromptProgramV1(**raw)


def test_policy_is_default_off_and_capability_projection_is_network_free() -> None:
    policy = DspyOptimizationPolicy()
    with pytest.raises(PermissionError, match="disabled"):
        policy.admit(spec())
    projection = DspyEngineCapabilityService(policy).projection()
    assert projection["state"] == "disabled"
    assert projection["human_intervention_required"] is False
    with pytest.raises(ValueError, match="unsafe_capability_denied"):
        DspyOptimizationPolicy.from_mapping({"allow_pickle": True})
