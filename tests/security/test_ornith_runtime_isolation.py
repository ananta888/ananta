from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_policy_has_no_privilege_egress_or_telemetry() -> None:
    policy = json.loads((ROOT / "config/security/ornith-runtime-policy.v1.json").read_text())

    assert policy["network"] == {"default": "deny", "allowed_destinations": []}
    assert policy["container"]["privileged"] is False
    assert policy["container"]["docker_socket"] is False
    assert policy["container"]["read_only_root"] is True
    assert policy["container"]["no_new_privileges"] is True
    assert policy["telemetry"]["enabled"] is False
    assert policy["limits"]["swap_growth_allowed"] is False
    assert policy["response"]["reasoning_authorizes_actions"] is False


def test_no_runtime_profile_enables_remote_code_or_production_default() -> None:
    for path in (ROOT / "config/runtime").glob("ornith-*.json"):
        profile = json.loads(path.read_text())
        assert profile["production_default_allowed"] is False
        assert all(runtime["remote_code_allowed"] is False for runtime in profile["runtimes"])
