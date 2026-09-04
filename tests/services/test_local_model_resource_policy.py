from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.local_model_resource_policy import (
    LocalModelResourcePolicy,
    LocalModelRuntimeProfileLoader,
    LocalResourceObservation,
)
from ananta_contracts.local_model_evaluation import LocalModelRuntimeProfile

ROOT = Path(__file__).resolve().parents[2]
GiB = 1024**3


def profile(name: str) -> LocalModelRuntimeProfile:
    return LocalModelRuntimeProfileLoader().load(ROOT / "config/runtime" / name)


def resources(**overrides) -> LocalResourceObservation:
    values = {
        "gpu_name": "NVIDIA GeForce RTX 3080",
        "total_vram_bytes": 10 * GiB,
        "free_vram_bytes": 10 * GiB,
        "total_ram_bytes": 64 * GiB,
        "available_ram_bytes": 56 * GiB,
        "swap_used_bytes": 5 * GiB,
        "thermal_throttling": False,
    }
    values.update(overrides)
    return LocalResourceObservation(**values)


def test_9b_profile_admits_8k_and_preserves_swap_baseline() -> None:
    decision = LocalModelResourcePolicy().evaluate(
        profile("ornith-1.5-9b-rtx3080.v1.json"),
        context_tokens=8192,
        resources=resources(),
    )

    assert decision.admitted is True
    assert decision.reserve_vram_bytes == int(10 * GiB * 0.15)
    assert decision.swap_baseline_bytes == 5 * GiB


def test_9b_profile_rejects_32k_when_headroom_is_below_15_percent() -> None:
    decision = LocalModelResourcePolicy().evaluate(
        profile("ornith-1.5-9b-rtx3080.v1.json"),
        context_tokens=32768,
        resources=resources(),
    )

    assert decision.admitted is False
    assert decision.reason_code == "local_model_vram_reserve_insufficient"


def test_35b_profile_admits_bounded_offload_but_never_stress_context() -> None:
    loaded = profile("ornith-1.5-35b-a3b-64gb-offload.v1.json")

    admitted = LocalModelResourcePolicy().evaluate(
        loaded, context_tokens=8192, resources=resources()
    )
    stress = LocalModelResourcePolicy().evaluate(
        loaded, context_tokens=262144, resources=resources()
    )

    assert admitted.admitted is True
    assert stress.admitted is False
    assert stress.reason_code in {
        "local_model_vram_reserve_insufficient",
        "local_model_ram_reserve_insufficient",
        "local_model_context_stress_only",
    }


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"gpu_name": "Other GPU"}, "local_model_gpu_mismatch"),
        ({"total_vram_bytes": 8 * GiB}, "local_model_total_vram_insufficient"),
        ({"total_ram_bytes": 31 * GiB}, "local_model_total_ram_insufficient"),
        ({"thermal_throttling": True}, "local_model_thermal_throttling"),
    ],
)
def test_resource_policy_fails_closed(overrides, reason) -> None:
    decision = LocalModelResourcePolicy().evaluate(
        profile("ornith-1.5-9b-rtx3080.v1.json"),
        context_tokens=8192,
        resources=resources(**overrides),
    )

    assert decision.admitted is False
    assert decision.reason_code == reason


def test_profiles_keep_remote_code_and_production_default_disabled() -> None:
    for name in (
        "ornith-1.5-9b-rtx3080.v1.json",
        "ornith-1.5-35b-a3b-64gb-offload.v1.json",
    ):
        loaded = profile(name)
        assert loaded.production_default_allowed is False
        assert loaded.requires_no_swap_growth is True
        assert all(runtime.remote_code_allowed is False for runtime in loaded.runtimes)
        vllm = next(runtime for runtime in loaded.runtimes if runtime.runtime_id == "vllm")
        assert vllm.state == "incompatible"
        assert "upstream_recipe_requires_remote_code" in vllm.reason_codes


def test_profile_contract_rejects_less_than_15_percent_reserve() -> None:
    path = ROOT / "config/runtime/ornith-1.5-9b-rtx3080.v1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["minimum_reserve_fraction"] = 0.149

    with pytest.raises(ValueError):
        LocalModelRuntimeProfile.model_validate(payload)
