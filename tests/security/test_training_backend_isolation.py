from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

COMPOSES = tuple(Path("docker/compose-next").glob("compose.training-*.yml"))


@pytest.mark.parametrize("path", COMPOSES, ids=lambda path: path.stem)
def test_optional_training_compose_is_internal_unprivileged_and_not_published(path: Path) -> None:
    payload = yaml.safe_load(path.read_text())
    assert payload["networks"]["lora-training-control"]["internal"] is True
    service = next(iter(payload["services"].values()))
    assert "ports" not in service
    assert service.get("privileged") is not True
    assert service.get("network_mode") != "host"
    assert "/var/run/docker.sock" not in json.dumps(service)
    assert service["environment"]["ANANTA_LORA_TRAINING_BACKENDS"] in path.stem


def test_egress_policy_is_deny_by_default_for_every_new_backend() -> None:
    policy = json.loads(Path("config/policies/training-backend-egress.v1.json").read_text())
    assert policy["default_action"] == "deny"
    assert policy["request_controlled_egress"] is False
    assert set(policy["training_profiles"]) == {"autotrain", "axolotl", "llamafactory", "torchtune"}
    assert all(
        profile == {"network": "none", "telemetry": False, "uploads": False}
        for profile in policy["training_profiles"].values()
    )
