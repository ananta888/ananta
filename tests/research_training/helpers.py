from __future__ import annotations

from pathlib import Path
from typing import Any

from agent.services.research_training_capability_service import ResearchTrainingCapabilityService
from agent.services.research_training_policy import ResearchTrainingPolicy
from agent.services.research_training_recipe_service import ResearchTrainingRecipeService
from agent.services.research_training_run_service import ResearchTrainingRunService
from agent.services.research_training_state_store import ResearchTrainingStateStore
from ananta_contracts.research_training import STAGE_CAPABILITIES

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def policy(*, automatic_release: bool = True) -> ResearchTrainingPolicy:
    return ResearchTrainingPolicy.from_mapping(
        {
            "schema": "ananta.research-training-policy.v1",
            "enabled": True,
            "mode": "mock",
            "automatic_release_enabled": automatic_release,
            "allowed_model_families": ["tiny-local"],
            "max_gpu_hours": 10,
            "max_storage_bytes": 10_737_418_240,
            "max_estimated_cost_microunits": 1_000_000,
            "max_world_size": 2,
            "max_stages": 16,
            "max_artifact_bytes": 1_048_576,
            "human_intervention_required": False,
        }
    )


def recipe_request() -> dict[str, Any]:
    return {
        "recipe_id": "tiny-depth",
        "model_family": "tiny-local",
        "architecture": "decoder-transformer",
        "depth": 2,
        "context_length": 128,
        "vocab_size": 256,
        "max_steps": 2,
        "seed": 7,
        "precision": "float32",
        "world_size": 1,
        "allow_rl": False,
    }


def spec(recipes: ResearchTrainingRecipeService, *, automatic_release: bool = True) -> dict[str, Any]:
    resolved = recipes.resolve(recipe_request())
    recipe = {
        key: value
        for key, value in resolved.items()
        if key not in {"recipe_digest", "resolution_is_deterministic"}
    }
    return {
        "schema": "ananta.research-training-run.v1",
        "spec_id": "tiny-run",
        "tenant_id": "tenant-a",
        "mode": "dry_run",
        "dataset_manifest_digest": DIGEST_A,
        "source_revision_digest": DIGEST_B,
        "recipe": recipe,
        "pipeline": {
            "schema": "ananta.research-training-pipeline.v1",
            "pipeline_id": "tiny-pipeline",
            "pipeline_version": "v1",
            "stages": [
                {
                    "stage_id": "tokenizer",
                    "kind": "tokenizer_train",
                    "dependencies": [],
                    "required_capability": "tokenizer_training",
                    "max_attempts": 2,
                    "timeout_seconds": 60,
                },
                {
                    "stage_id": "export",
                    "kind": "export",
                    "dependencies": ["tokenizer"],
                    "required_capability": "model_export",
                    "max_attempts": 2,
                    "timeout_seconds": 60,
                },
            ],
            "automatic_release": automatic_release,
        },
        "budget": {"gpu_hours": 1, "storage_bytes": 10_737_418_240, "estimated_cost_microunits": 0},
    }


def services(path: Path) -> tuple[ResearchTrainingRunService, ResearchTrainingRecipeService]:
    configured_policy = policy()
    recipes = ResearchTrainingRecipeService(configured_policy)
    capabilities = ResearchTrainingCapabilityService(configured_policy)
    capabilities.report_worker(
        {
            "state": "available",
            "reason_code": None,
            "engine_version": "mock-v1",
            "capabilities": sorted(set(STAGE_CAPABILITIES.values())),
            "gpu_profiles": ["none"],
            "network_probe_performed": False,
        }
    )
    return (
        ResearchTrainingRunService(
            ResearchTrainingStateStore(path),
            policy=configured_policy,
            capabilities=capabilities,
            recipes=recipes,
            signing_key=b"r" * 32,
        ),
        recipes,
    )
