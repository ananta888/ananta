from __future__ import annotations

import pytest

from agent.services.dendritic_memory_policy import DendriticMemoryPolicy
from ananta_contracts.dendritic_memory import DendriticExperimentConfigV1, DendriticJobSpecV1
from tests.dendritic_memory.helpers import config, spec


def test_contract_is_closed_bounded_and_separate_from_lora_v2() -> None:
    parsed = DendriticJobSpecV1.from_mapping(spec().to_dict())
    assert parsed.schema == "ananta.dendritic-memory-job.v1"
    with pytest.raises(ValueError, match="unknown_field"):
        DendriticJobSpecV1.from_mapping({**spec().to_dict(), "python_class": "unsafe"})
    with pytest.raises(ValueError, match="bound_invalid"):
        DendriticExperimentConfigV1(**{**config().to_dict(), "branch_count": 65})
    with pytest.raises(ValueError, match="target_layer_invalid"):
        DendriticExperimentConfigV1(**{**config().to_dict(), "target_layers": ["../unsafe"]})


def test_policy_is_strict_default_off_and_human_free() -> None:
    policy = DendriticMemoryPolicy.from_mapping(
        {
            "schema": "ananta.dendritic-memory-policy.v1",
            "enabled": False,
            "mode": "disabled",
            "runtime_enabled": False,
            "automatic_activation_enabled": False,
        }
    )
    with pytest.raises(PermissionError, match="disabled"):
        policy.admit(spec())
    with pytest.raises(ValueError, match="human_intervention"):
        DendriticMemoryPolicy.from_mapping({"human_intervention_required": True})
    with pytest.raises(ValueError, match="enabled_mode"):
        DendriticMemoryPolicy.from_mapping({"enabled": True, "mode": "disabled"})
