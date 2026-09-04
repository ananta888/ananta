"""Transparent recipe resolution, scaling sweeps and resource preflight."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from agent.services.research_training_policy import ResearchTrainingPolicy
from ananta_contracts.research_training import ResearchRunSpecV1, ResearchTrainingRecipeV1, canonical_digest


class ResearchTrainingRecipeService:
    _ARCHITECTURES = {
        "decoder-transformer": {"minimum_depth": 1, "maximum_depth": 128},
    }

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
        model_family = str(request["model_family"])
        architecture = str(request["architecture"])
        if self._policy.allowed_model_families and model_family not in self._policy.allowed_model_families:
            raise ValueError("research_recipe_model_family_unsupported")
        support = self._ARCHITECTURES.get(architecture)
        if support is None:
            raise ValueError("research_recipe_architecture_unsupported")
        depth = int(request["depth"])
        if not support["minimum_depth"] <= depth <= support["maximum_depth"]:
            raise ValueError("research_recipe_depth_invalid")
        hidden_size = max(256, min(8192, depth * 64))
        attention_heads = max(4, min(128, hidden_size // 64))
        context_length = int(request["context_length"])
        tokens_per_batch = max(1024, min(1_048_576, context_length * max(1, 32 // max(1, depth))))
        payload = {
            "schema": ResearchTrainingRecipeV1.SCHEMA,
            "recipe_id": request["recipe_id"],
            "recipe_version": "depth-v1",
            "model_family": model_family,
            "architecture": architecture,
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

    def resolve_explicit(self, recipe: Mapping[str, Any]) -> dict[str, Any]:
        parsed = ResearchTrainingRecipeV1.from_mapping(recipe)
        if parsed.architecture not in self._ARCHITECTURES:
            raise ValueError("research_recipe_architecture_unsupported")
        if self._policy.allowed_model_families and parsed.model_family not in self._policy.allowed_model_families:
            raise ValueError("research_recipe_model_family_unsupported")
        return {
            **parsed.to_dict(),
            "recipe_digest": parsed.digest,
            "resolution_is_deterministic": True,
            "resolution_mode": "explicit",
        }

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

    def preflight(
        self,
        spec: ResearchRunSpecV1,
        *,
        hardware_profiles: Sequence[Mapping[str, Any]] = (),
    ) -> dict[str, Any]:
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
        profiles = [
            self._hardware_fit(profile, estimated_vram_per_worker, spec.recipe.world_size)
            for profile in hardware_profiles
        ]
        compatible_profiles = [item for item in profiles if item["compatible"]]
        if hardware_profiles and not compatible_profiles:
            reasons.append("research_preflight_hardware_unavailable")
        stage_estimates = [
            {
                "stage_id": stage.stage_id,
                "kind": stage.kind,
                "estimated_gpu_hours": estimated_gpu_hours
                if stage.kind in {"pretrain", "sft", "rl"}
                else round(estimated_gpu_hours * 0.1, 6),
                "estimated_storage_bytes": estimated_storage
                if stage.kind in {"pretrain", "sft", "rl", "export"}
                else min(estimated_storage, 16 * 1024 * 1024),
            }
            for stage in spec.pipeline.stages
        ]
        aggregate_gpu_hours = round(sum(item["estimated_gpu_hours"] for item in stage_estimates), 6)
        aggregate_storage = sum(item["estimated_storage_bytes"] for item in stage_estimates)
        if aggregate_gpu_hours > spec.budget.gpu_hours:
            reasons.append("research_preflight_aggregate_gpu_budget_exceeded")
        if aggregate_storage > spec.budget.storage_bytes:
            reasons.append("research_preflight_aggregate_storage_budget_exceeded")
        flops = 6 * estimated_parameters * spec.recipe.max_steps * spec.recipe.context_length
        return {
            "schema": "ananta.research-training-preflight.v1",
            "admissible": not reasons,
            "reason_codes": sorted(set(reasons)),
            "estimated_parameters": estimated_parameters,
            "estimated_vram_bytes_per_worker": estimated_vram_per_worker,
            "estimated_storage_bytes": estimated_storage,
            "estimated_gpu_hours": estimated_gpu_hours,
            "estimated_training_flops": flops,
            "aggregate_estimated_gpu_hours": aggregate_gpu_hours,
            "aggregate_estimated_storage_bytes": aggregate_storage,
            "stage_estimates": stage_estimates,
            "hardware_profiles": profiles,
            "compatible_hardware_profiles": [item["profile_id"] for item in compatible_profiles],
            "smaller_recipe_suggestion": self._smaller_recipe(spec),
            "worker_call_performed": False,
            "model_download_performed": False,
            "human_intervention_required": False,
        }

    @staticmethod
    def _hardware_fit(
        value: Mapping[str, Any], required_vram: int, world_size: int
    ) -> dict[str, Any]:
        if set(value) != {"profile_id", "gpu_count", "vram_bytes_per_gpu", "throughput_flops"}:
            raise ValueError("research_hardware_profile_fields_invalid")
        profile_id = str(value["profile_id"])
        gpu_count = int(value["gpu_count"])
        vram = int(value["vram_bytes_per_gpu"])
        throughput = float(value["throughput_flops"])
        if not profile_id or gpu_count < 0 or vram < 0 or throughput <= 0:
            raise ValueError("research_hardware_profile_invalid")
        return {
            "profile_id": profile_id,
            "compatible": gpu_count >= world_size and vram >= required_vram,
            "gpu_count": gpu_count,
            "vram_bytes_per_gpu": vram,
        }

    @staticmethod
    def _smaller_recipe(spec: ResearchRunSpecV1) -> dict[str, int] | None:
        if spec.recipe.depth <= 1:
            return None
        return {
            "depth": max(1, spec.recipe.depth // 2),
            "context_length": max(128, spec.recipe.context_length // 2),
            "world_size": min(spec.recipe.world_size, 1),
        }


__all__ = ["ResearchTrainingRecipeService"]
