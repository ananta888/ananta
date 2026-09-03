"""Hub-owned phased rollout decisions for experimental research training."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ResearchTrainingRolloutPhase:
    phase_id: str
    order: int
    runtime_mode: str
    required_gates: tuple[str, ...]
    max_world_size: int


@dataclass(frozen=True, slots=True)
class ResearchTrainingRolloutPolicy:
    enabled: bool
    automatic_progression_enabled: bool
    kill_switch: bool
    current_phase: str
    phases: tuple[ResearchTrainingRolloutPhase, ...]
    upstream_watch: Mapping[str, Any]

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ResearchTrainingRolloutPolicy:
        expected = {
            "schema",
            "enabled",
            "automatic_progression_enabled",
            "kill_switch",
            "current_phase",
            "production_routes_enabled",
            "phases",
            "upstream_watch",
            "human_intervention_required",
        }
        if set(value) != expected or value.get("schema") != "ananta.research-training-rollout.v1":
            raise ValueError("research_rollout_fields_invalid")
        if any(
            not isinstance(value.get(field), bool)
            for field in (
                "enabled",
                "automatic_progression_enabled",
                "kill_switch",
                "production_routes_enabled",
                "human_intervention_required",
            )
        ):
            raise ValueError("research_rollout_boolean_invalid")
        if value["production_routes_enabled"] is not False:
            raise ValueError("research_rollout_production_routes_forbidden")
        if value["human_intervention_required"] is not False:
            raise ValueError("research_rollout_human_intervention_forbidden")
        raw_phases = value.get("phases")
        if not isinstance(raw_phases, list) or not 1 <= len(raw_phases) <= 8:
            raise ValueError("research_rollout_phases_invalid")
        phases = tuple(cls._phase(item) for item in raw_phases)
        if [phase.order for phase in phases] != list(range(len(phases))):
            raise ValueError("research_rollout_phase_order_invalid")
        if len({phase.phase_id for phase in phases}) != len(phases):
            raise ValueError("research_rollout_phase_id_invalid")
        current_phase = str(value.get("current_phase") or "")
        if current_phase not in {phase.phase_id for phase in phases}:
            raise ValueError("research_rollout_current_phase_invalid")
        watch = value.get("upstream_watch")
        if not isinstance(watch, Mapping) or set(watch) != {
            "repository",
            "mode",
            "automatic_code_sync",
            "immutable_source_binding_required",
        }:
            raise ValueError("research_rollout_upstream_watch_invalid")
        if (
            watch.get("mode") != "review_only"
            or watch.get("automatic_code_sync") is not False
            or watch.get("immutable_source_binding_required") is not True
            or not str(watch.get("repository") or "").startswith("https://")
        ):
            raise ValueError("research_rollout_upstream_watch_invalid")
        return cls(
            enabled=value["enabled"],
            automatic_progression_enabled=value["automatic_progression_enabled"],
            kill_switch=value["kill_switch"],
            current_phase=current_phase,
            phases=phases,
            upstream_watch=dict(watch),
        )

    @staticmethod
    def _phase(value: Any) -> ResearchTrainingRolloutPhase:
        if not isinstance(value, Mapping) or set(value) != {
            "phase_id",
            "order",
            "runtime_mode",
            "required_gates",
            "max_world_size",
        }:
            raise ValueError("research_rollout_phase_fields_invalid")
        phase_id = str(value.get("phase_id") or "")
        order = value.get("order")
        runtime_mode = str(value.get("runtime_mode") or "")
        required = value.get("required_gates")
        max_world_size = value.get("max_world_size")
        if (
            not phase_id.startswith("phase_")
            or isinstance(order, bool)
            or not isinstance(order, int)
            or runtime_mode not in {"dry_run", "tiny_local", "single_gpu", "multi_gpu", "optional_rl"}
            or not isinstance(required, list)
            or not required
            or len(required) != len(set(required))
            or any(not isinstance(gate, str) or not gate for gate in required)
            or isinstance(max_world_size, bool)
            or not isinstance(max_world_size, int)
            or not 1 <= max_world_size <= 1024
        ):
            raise ValueError("research_rollout_phase_invalid")
        return ResearchTrainingRolloutPhase(phase_id, order, runtime_mode, tuple(required), max_world_size)


class ResearchTrainingRolloutService:
    """Evaluate progression and rollback without mutating non-research paths."""

    def __init__(self, policy: ResearchTrainingRolloutPolicy) -> None:
        self._policy = policy

    def evaluate(self, gate_results: Mapping[str, bool]) -> dict[str, Any]:
        if any(not isinstance(key, str) or not isinstance(result, bool) for key, result in gate_results.items()):
            raise ValueError("research_rollout_gate_results_invalid")
        phase = next(item for item in self._policy.phases if item.phase_id == self._policy.current_phase)
        if not self._policy.enabled:
            return self._decision(phase, phase, False, "research_rollout_disabled", phase.required_gates)
        if self._policy.kill_switch:
            return self._decision(phase, phase, False, "research_rollout_kill_switch_active", phase.required_gates)
        missing = tuple(gate for gate in phase.required_gates if gate_results.get(gate) is not True)
        if missing:
            return self._decision(phase, phase, False, "research_rollout_gates_incomplete", missing)
        if not self._policy.automatic_progression_enabled:
            return self._decision(phase, phase, False, "research_rollout_progression_disabled", ())
        next_phase = next((item for item in self._policy.phases if item.order == phase.order + 1), phase)
        advanced = next_phase is not phase
        reason = "research_rollout_advanced" if advanced else "research_rollout_final_phase_verified"
        return self._decision(phase, next_phase, advanced, reason, ())

    @staticmethod
    def rollback(*, reason_code: str) -> dict[str, Any]:
        if not reason_code or len(reason_code) > 128:
            raise ValueError("research_rollout_rollback_reason_invalid")
        return {
            "schema": "ananta.research-training-rollback.v1",
            "reason_code": reason_code,
            "research_training_enabled": False,
            "research_runtime_enabled": False,
            "cancel_new_research_admissions": True,
            "adapter_training_changed": False,
            "production_routes_changed": False,
            "human_intervention_required": False,
        }

    @staticmethod
    def _decision(
        current: ResearchTrainingRolloutPhase,
        target: ResearchTrainingRolloutPhase,
        advanced: bool,
        reason_code: str,
        missing_gates: tuple[str, ...],
    ) -> dict[str, Any]:
        return {
            "schema": "ananta.research-training-rollout-decision.v1",
            "current_phase": current.phase_id,
            "target_phase": target.phase_id,
            "advanced": advanced,
            "reason_code": reason_code,
            "missing_gates": list(missing_gates),
            "production_routes_enabled": False,
            "human_intervention_required": False,
        }


__all__ = [
    "ResearchTrainingRolloutPhase",
    "ResearchTrainingRolloutPolicy",
    "ResearchTrainingRolloutService",
]
