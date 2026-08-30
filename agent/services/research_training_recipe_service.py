"""Transparent recipe resolution, scaling sweeps and resource preflight."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.research_training_policy import ResearchTrainingPolicy
from ananta_contracts.research_training import ResearchRunSpecV1, ResearchTrainingRecipeV1, canonical_digest


class ResearchTrainingRecipeService:
    def __init__(self, policy: ResearchTrainingPolicy) -> None:
        self._policy = policy

    def resolve(self, request: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "recipe_id",
            "model_family",
            "architecture",
            "depth",
            "context_length",
            "vocab_size",
            "max_steps",
            "seed",
            "precision",
            "world_size",
            "allow_rl",
        }
        if set(request) != expected:
            raise ValueError("research_recipe_request_fields_invalid")
        depth = int(request["depth"])
        if not 1 <= depth <= 128:
            raise ValueError("research_recipe_depth_invalid")
        hidden_size = max(256, min(8192, depth * 64))
        attention_heads = max(4, min(128, hidden_size // 64))
        context_length = int(request["context_length"])
        tokens_per_batch = max(1024, min(1_048_576, context_length * max(1, 32 // max(1, depth))))
        payload = {
            "schema": ResearchTrainingRecipeV1.SCHEMA,
            "recipe_id": request["recipe_id"],
            "recipe_version": "depth-v1",
            "model_family": request["model_family"],
            "architecture": request["architecture"],
            "depth": depth,
            "context_length": context_length,
            "vocab_size": int(request["vocab_size"]),
            "max_steps": int(request["max_steps"]),
            "seed": int(request["seed"]),
            "precision": request["precision"],
            "world_size": int(request["world_size"]),
            "allow_rl": request["allow_rl"],
            "resolved_hyperparameters": {
                "num_layers": depth,
                "hidden_size": hidden_size,
                "attention_heads": attention_heads,
                "tokens_per_batch": tokens_per_batch,
                "learning_rate": round(min(0.001, 0.02 / hidden_size**0.5), 10),
                "weight_decay": 0.1,
            },
        }
        recipe = ResearchTrainingRecipeV1.from_mapping(payload)
        return {**recipe.to_dict(), "recipe_digest": recipe.digest, "resolution_is_deterministic": True}

    def sweep(self, request: Mapping[str, Any], depths: Sequence[int]) -> dict[str, Any]:
        if not 1 <= len(depths) <= 16 or len(set(depths)) != len(depths):
            raise ValueError("research_sweep_depths_invalid")
        recipes = [self.resolve({**dict(request), "depth": int(depth)}) for depth in depths]
        return {
            "schema": "ananta.research-training-sweep.v1",
            "recipes": recipes,
            "sweep_digest": canonical_digest(recipes),
            "human_intervention_required": False,
        }

    def preflight(self, spec: ResearchRunSpecV1) -> dict[str, Any]:
        hyperparameters = spec.recipe.resolved_hyperparameters
        hidden = int(hyperparameters.get("hidden_size") or 0)
        layers = int(hyperparameters.get("num_layers") or spec.recipe.depth)
        estimated_parameters = max(1, 12 * layers * hidden * hidden + spec.recipe.vocab_size * hidden)
        bytes_per_parameter = {"float32": 4, "bfloat16": 2, "float16": 2}[spec.recipe.precision]
        estimated_training_bytes = estimated_parameters * (bytes_per_parameter + 12)
        estimated_vram_per_worker = (estimated_training_bytes + spec.recipe.world_size - 1) // spec.recipe.world_size
        estimated_storage = estimated_parameters * bytes_per_parameter * 4
        estimated_gpu_hours = round(
            spec.recipe.max_steps * max(1, spec.recipe.context_length) * estimated_parameters / 1.5e18,
            6,
        )
        reasons = self._policy.denial_reasons(spec)
        if estimated_storage > spec.budget.storage_bytes:
            reasons.append("research_preflight_storage_estimate_exceeded")
        if estimated_gpu_hours > spec.budget.gpu_hours:
            reasons.append("research_preflight_gpu_estimate_exceeded")
        return {
            "schema": "ananta.research-training-preflight.v1",
            "admissible": not reasons,
            "reason_codes": sorted(set(reasons)),
            "estimated_parameters": estimated_parameters,
            "estimated_vram_bytes_per_worker": estimated_vram_per_worker,
            "estimated_storage_bytes": estimated_storage,
            "estimated_gpu_hours": estimated_gpu_hours,
            "worker_call_performed": False,
            "model_download_performed": False,
            "human_intervention_required": False,
        }


__all__ = ["ResearchTrainingRecipeService"]
