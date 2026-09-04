"""Independent capability and isolation policy for risky research stages."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ananta_contracts.research_training import STAGE_CAPABILITIES, ResearchRunSpecV1, require_id
from ananta_contracts.research_training_data import ResearchDatasetManifestV1


@dataclass(frozen=True, slots=True)
class ResearchTrainingSafetyPolicy:
    enabled_capabilities: frozenset[str]
    maximum_dataset_bytes: int
    maximum_checkpoint_count: int
    maximum_checkpoint_bytes: int
    code_evaluation_enabled: bool
    rl_training_enabled: bool
    multi_gpu_training_enabled: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchTrainingSafetyPolicy:
        expected = {
            "schema",
            "enabled_capabilities",
            "maximum_dataset_bytes",
            "maximum_checkpoint_count",
            "maximum_checkpoint_bytes",
            "code_evaluation_enabled",
            "rl_training_enabled",
            "multi_gpu_training_enabled",
            "network_mode",
            "filesystem_scope",
            "human_intervention_required",
        }
        if set(value) != expected or value.get("schema") != "ananta.research-training-safety-policy.v1":
            raise ValueError("research_safety_policy_fields_invalid")
        raw = value.get("enabled_capabilities")
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) > 32:
            raise ValueError("research_safety_capabilities_invalid")
        capabilities = frozenset(require_id(item, "capability") for item in raw)
        known = set(STAGE_CAPABILITIES.values()) | {"multi_gpu_training", "code_exec_eval"}
        if capabilities - known:
            raise ValueError("research_safety_capability_unknown")
        booleans = ("code_evaluation_enabled", "rl_training_enabled", "multi_gpu_training_enabled")
        if any(not isinstance(value.get(field), bool) for field in booleans):
            raise ValueError("research_safety_boolean_invalid")
        if (
            value.get("human_intervention_required") is not False
            or value.get("network_mode") != "none"
            or value.get("filesystem_scope") != "task_workspace_only"
        ):
            raise ValueError("research_safety_isolation_invalid")
        policy = cls(
            enabled_capabilities=capabilities,
            maximum_dataset_bytes=int(value["maximum_dataset_bytes"]),
            maximum_checkpoint_count=int(value["maximum_checkpoint_count"]),
            maximum_checkpoint_bytes=int(value["maximum_checkpoint_bytes"]),
            code_evaluation_enabled=bool(value["code_evaluation_enabled"]),
            rl_training_enabled=bool(value["rl_training_enabled"]),
            multi_gpu_training_enabled=bool(value["multi_gpu_training_enabled"]),
        )
        if (
            not 1 <= policy.maximum_dataset_bytes <= 1 << 50
            or not 1 <= policy.maximum_checkpoint_count <= 10_000
            or not 1 <= policy.maximum_checkpoint_bytes <= 1 << 50
        ):
            raise ValueError("research_safety_limit_invalid")
        return policy

    def denial_reasons(
        self,
        *,
        spec: ResearchRunSpecV1,
        dataset: ResearchDatasetManifestV1,
        existing_checkpoint_count: int,
    ) -> list[str]:
        reasons: list[str] = []
        required = {stage.required_capability for stage in spec.pipeline.stages}
        if not required <= self.enabled_capabilities:
            reasons.append("research_safety_capability_disabled")
        if dataset.size_bytes > self.maximum_dataset_bytes:
            reasons.append("research_safety_dataset_size_exceeded")
        requested_checkpoints = sum(stage.kind in {"pretrain", "sft", "rl"} for stage in spec.pipeline.stages)
        if existing_checkpoint_count + requested_checkpoints > self.maximum_checkpoint_count:
            reasons.append("research_safety_checkpoint_count_exceeded")
        if any(stage.kind == "rl" for stage in spec.pipeline.stages) and not self.rl_training_enabled:
            reasons.append("research_safety_rl_disabled")
        if spec.recipe.world_size > 1 and not self.multi_gpu_training_enabled:
            reasons.append("research_safety_multi_gpu_disabled")
        return reasons


__all__ = ["ResearchTrainingSafetyPolicy"]
