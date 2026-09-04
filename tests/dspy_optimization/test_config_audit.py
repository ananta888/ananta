from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.services.dspy_engine_capability_service import DspyEngineCapabilityService
from agent.services.dspy_optimization_config_service import DspyOptimizationConfigService


def test_default_config_is_dependency_free_digest_bound_and_projected() -> None:
    raw = json.loads((Path(__file__).parents[2] / "config/dspy/optimization_defaults.v1.json").read_text())
    policy, audit = DspyOptimizationConfigService().load(raw, enabled=False, mode="disabled")
    assert audit["policy_digest"] == policy.digest
    assert audit["secret_fields_present"] is False
    projection = DspyEngineCapabilityService(policy).projection()
    assert projection["state"] == "disabled"
    assert projection["provider_profiles"] == ["local.default"]
    assert projection["metric_sets"] == ["deterministic-v1"]
    assert projection["policy_digest"] == policy.digest


def test_config_rejects_unknown_profiles_metrics_duplicates_and_secrets() -> None:
    service = DspyOptimizationConfigService()
    base = {
        "schema": "ananta.dspy-optimization-policy.v1",
        "enabled": False,
        "mode": "disabled",
    }
    with pytest.raises(ValueError, match="provider_profile_invalid"):
        service.load({**base, "allowed_provider_profiles": ["untrusted"]}, enabled=False, mode="disabled")
    with pytest.raises(ValueError, match="metric_set_invalid"):
        service.load({**base, "allowed_metric_sets": ["model-defined"]}, enabled=False, mode="disabled")
    with pytest.raises(ValueError, match="duplicate_capability"):
        service.load({**base, "allowed_retrievers": ["codecompass", "codecompass"]}, enabled=False, mode="disabled")
    with pytest.raises(ValueError, match="unknown_field"):
        service.load({**base, "api_key": "secret"}, enabled=False, mode="disabled")
