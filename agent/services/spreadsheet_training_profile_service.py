"""Closed Spreadsheet Studio LoRA/QLoRA profile validation and projection."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from agent.services.spreadsheet_training_task_family import SpreadsheetTrainingTaskFamilyStrategy
from ananta_contracts.spreadsheet_studio import canonical_digest, require_digest, require_id


class SpreadsheetTrainingProfileService:
    """Validates one immutable profile and derives the existing ML-Intern command."""

    PROFILE_SCHEMA = "ananta.spreadsheet-training-profile.v1"
    PROFILE_VERSION = "spreadsheet-lora-profile.v1"
    BACKENDS = frozenset({"mock", "peft_trl", "unsloth"})
    METHODS = frozenset({"lora", "qlora"})
    GPU_PROFILES = frozenset({"none", "generic-safe", "rtx3080-safe"})
    FIELDS = frozenset(
        {
            "schema",
            "profile_id",
            "profile_version",
            "base_model",
            "model_digest",
            "backend",
            "method",
            "quantization",
            "lora_rank",
            "lora_alpha",
            "lora_dropout",
            "target_modules",
            "max_sequence_length",
            "max_cells_per_example",
            "seed",
            "max_steps",
            "num_train_epochs",
            "learning_rate",
            "batch_size",
            "gradient_accumulation_steps",
            "evaluation_steps",
            "early_stopping_patience",
            "checkpoint_interval_steps",
            "resume_allowed",
            "gpu_profile",
            "resource_profile_digest",
            "dataset_recipe_digest",
            "split_lock_digest",
            "action_schema_digest",
            "serializer_digest",
            "policy_digest",
            "profile_digest",
        }
    )

    def __init__(self, *, strategy: SpreadsheetTrainingTaskFamilyStrategy | None = None) -> None:
        self._strategy = strategy or SpreadsheetTrainingTaskFamilyStrategy()

    def project(
        self,
        value: Any,
        *,
        dataset: Mapping[str, Any],
        admission: Mapping[str, Any],
    ) -> dict[str, Any]:
        profile = self._profile(value)
        recipe = dict(dataset.get("recipe_manifest") or {})
        materialization = dict(dataset.get("materialization") or {})
        expected = {
            "base_model": admission.get("base_model"),
            "model_digest": admission.get("model_digest"),
            "backend": admission.get("resource_backend"),
            "gpu_profile": admission.get("resource_profile_id"),
            "resource_profile_digest": admission.get("resource_profile_digest"),
            "dataset_recipe_digest": recipe.get("recipe_digest"),
            "split_lock_digest": dict(dataset.get("split_lock") or {}).get("split_lock_digest"),
            "action_schema_digest": self._strategy.schema_digest,
            "serializer_digest": self._strategy.serializer_digest,
            "policy_digest": canonical_digest({"policy_version": dataset.get("policy_version")}),
        }
        for field, expected_value in expected.items():
            if profile.get(field) != expected_value:
                raise PermissionError(f"spreadsheet_training_profile_{field}_mismatch")
        observed_cells = materialization.get("maximum_cells_per_record")
        if (
            isinstance(observed_cells, bool)
            or not isinstance(observed_cells, int)
            or observed_cells < 0
            or observed_cells > profile["max_cells_per_example"]
        ):
            raise PermissionError("spreadsheet_training_profile_cell_budget_exceeded")
        hyperparameters = {
            "lora_rank": profile["lora_rank"],
            "lora_alpha": profile["lora_alpha"],
            "lora_dropout": profile["lora_dropout"],
            "target_modules": list(profile["target_modules"]),
            "learning_rate": profile["learning_rate"],
            "batch_size": profile["batch_size"],
            "gradient_accumulation_steps": profile["gradient_accumulation_steps"],
            "max_steps": profile["max_steps"],
            "num_train_epochs": profile["num_train_epochs"],
            "max_seq_length": profile["max_sequence_length"],
            "load_in_4bit": profile["quantization"] == "4bit",
            "evaluation_steps": profile["evaluation_steps"],
            "early_stopping_patience": profile["early_stopping_patience"],
            "seed": profile["seed"],
        }
        governance = {
            "training_profile_digest": profile["profile_digest"],
            "base_model_digest": profile["model_digest"],
            "dataset_manifest_digest": dataset["digest"],
            "dataset_artifact_digest": dataset["dataset_digest"],
            "dataset_recipe_digest": profile["dataset_recipe_digest"],
            "split_lock_digest": profile["split_lock_digest"],
            "action_schema_digest": profile["action_schema_digest"],
            "serializer_digest": profile["serializer_digest"],
            "policy_digest": profile["policy_digest"],
            "resource_profile_digest": profile["resource_profile_digest"],
            "training_admission_digest": admission["admission_digest"],
        }
        governance["governance_digest"] = canonical_digest(governance)
        return {
            "profile": profile,
            "backend": profile["backend"],
            "base_model": profile["base_model"],
            "method": profile["method"],
            "gpu_profile": profile["gpu_profile"],
            "hyperparameters": hyperparameters,
            "spreadsheet_governance": governance,
        }

    def _profile(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != self.FIELDS:
            raise ValueError("spreadsheet_training_profile_fields_invalid")
        profile = dict(value)
        supplied = require_digest(profile.pop("profile_digest", None), "profile_digest")
        if canonical_digest(profile) != supplied:
            raise ValueError("spreadsheet_training_profile_digest_mismatch")
        if (
            profile.get("schema") != self.PROFILE_SCHEMA
            or profile.get("profile_version") != self.PROFILE_VERSION
            or profile.get("backend") not in self.BACKENDS
            or profile.get("method") not in self.METHODS
            or profile.get("gpu_profile") not in self.GPU_PROFILES
            or not isinstance(profile.get("resume_allowed"), bool)
        ):
            raise ValueError("spreadsheet_training_profile_invalid")
        require_id(profile.get("profile_id"), "profile_id")
        self._text(profile.get("base_model"), maximum=512)
        for field in (
            "model_digest",
            "resource_profile_digest",
            "dataset_recipe_digest",
            "split_lock_digest",
            "action_schema_digest",
            "serializer_digest",
            "policy_digest",
        ):
            require_digest(profile.get(field), field)
        integer_bounds = {
            "lora_rank": (1, 256),
            "lora_alpha": (1, 512),
            "max_sequence_length": (128, 32_768),
            "max_cells_per_example": (1, 100_000),
            "seed": (0, 2**31 - 1),
            "max_steps": (1, 1_000_000),
            "batch_size": (1, 128),
            "gradient_accumulation_steps": (1, 1024),
            "evaluation_steps": (1, 1_000_000),
            "early_stopping_patience": (0, 1000),
            "checkpoint_interval_steps": (1, 1_000_000),
        }
        for field, (minimum, maximum) in integer_bounds.items():
            child = profile.get(field)
            if isinstance(child, bool) or not isinstance(child, int) or not minimum <= child <= maximum:
                raise ValueError(f"spreadsheet_training_profile_{field}_invalid")
        number_bounds = {
            "lora_dropout": (0.0, 0.9),
            "num_train_epochs": (0.01, 1000.0),
            "learning_rate": (1e-7, 1.0),
        }
        for field, (minimum, maximum) in number_bounds.items():
            child = profile.get(field)
            if (
                isinstance(child, bool)
                or not isinstance(child, (int, float))
                or not math.isfinite(float(child))
                or not minimum <= float(child) <= maximum
            ):
                raise ValueError(f"spreadsheet_training_profile_{field}_invalid")
        modules = profile.get("target_modules")
        if (
            not isinstance(modules, list)
            or not 1 <= len(modules) <= 64
        ):
            raise ValueError("spreadsheet_training_profile_target_modules_invalid")
        normalized_modules = []
        for module in modules:
            try:
                normalized_modules.append(require_id(module, "target_module"))
            except ValueError as exc:
                raise ValueError("spreadsheet_training_profile_target_modules_invalid") from exc
        if len(set(normalized_modules)) != len(normalized_modules):
            raise ValueError("spreadsheet_training_profile_target_modules_invalid")
        expected_quantization = "4bit" if profile["method"] == "qlora" else "none"
        if profile.get("quantization") != expected_quantization:
            raise ValueError("spreadsheet_training_profile_quantization_invalid")
        if profile["checkpoint_interval_steps"] != profile["evaluation_steps"]:
            raise ValueError("spreadsheet_training_profile_checkpoint_interval_invalid")
        return {**profile, "profile_digest": supplied}

    @staticmethod
    def _text(value: Any, *, maximum: int) -> str:
        normalized = str(value or "").strip()
        if not 1 <= len(normalized) <= maximum or any(ord(character) < 32 for character in normalized):
            raise ValueError("spreadsheet_training_profile_text_invalid")
        return normalized


__all__ = ["SpreadsheetTrainingProfileService"]
