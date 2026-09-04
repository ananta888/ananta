from __future__ import annotations

from typing import Any

from ananta_contracts.dspy_optimization import OptimizationSpecV1, PromptProgramV1
from ananta_contracts.provider_execution import ProviderExecutionBinding

DIGEST = "a" * 64


def binding() -> dict[str, Any]:
    return ProviderExecutionBinding(
        provider_id="ollama",
        model_id="model-1",
        source="hub_policy",
        reason_code="explicit_test_binding",
    ).to_dict()


def price_profiles() -> dict[str, dict[str, int]]:
    return {
        binding()["binding_id"]: {
            "input_micros_per_million": 0,
            "output_micros_per_million": 0,
            "reasoning_micros_per_million": 0,
        }
    }


def spec(*, tenant_id: str = "tenant-1", optimizer_id: str = "labeled_few_shot") -> OptimizationSpecV1:
    return OptimizationSpecV1(
        tenant_id=tenant_id,
        spec_id="spec-1",
        program_kind="planning_structured_tasks",
        dataset_manifest_digest=DIGEST,
        metric_set_digest="b" * 64,
        optimizer_id=optimizer_id,
        optimizer_config_digest="c" * 64,
        seed=7,
        provider_bindings={"student": binding()},
        budgets={
            "max_model_calls": 10,
            "max_tokens": 1_000,
            "max_cost_micros": 0,
            "timeout_seconds": 30,
            "max_concurrency": 1,
            "max_dataset_records": 100,
            "max_artifact_bytes": 100_000,
            "max_retries": 1,
        },
    )


def program(*, tenant_id: str = "tenant-1", suffix: str = "baseline") -> PromptProgramV1:
    return PromptProgramV1(
        tenant_id=tenant_id,
        program_id=f"planning-{suffix}",
        program_kind="planning_structured_tasks",
        module_graph=[
            {
                "id": "main",
                "module": "predict",
                "inputs": ["goal", "constraints"],
                "outputs": ["tasks"],
                "depends_on": [],
            }
        ],
        signatures=[
            {
                "id": "planning-v1",
                "instructions": "Return bounded structured tasks.",
                "input_fields": ["goal", "constraints"],
                "output_fields": ["tasks"],
            }
        ],
        demonstrations=[],
        model_roles={"student": "provider-binding"},
        source_program_digest="d" * 64,
        exporter_version="native-v1",
    )
