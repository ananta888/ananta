from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_redteam_is_synthetic_non_executing_and_not_release_evidence() -> None:
    import yaml

    matrix = yaml.safe_load((ROOT / "benchmarks/security/abliterated-redteam.yaml").read_text())
    assert matrix["synthetic_data_only"] is True
    assert matrix["real_credentials_allowed"] is False
    assert matrix["tool_execution_allowed"] is False
    assert matrix["network_allowed"] is False
    assert matrix["production_claim"] is False


def test_trust_policy_and_sandbox_have_matching_denials() -> None:
    policy = json.loads((ROOT / "config/security/model-trust-policy.v1.json").read_text())
    sandbox = json.loads((ROOT / "config/security/unsafe-research-sandbox.v1.json").read_text())
    assert policy["network_allowed"] is False and sandbox["network"] == "none"
    assert policy["writes_allowed"] is False and sandbox["filesystem"]["workspace"] == "read_only"
    assert policy["default_tools_allowed"] is False and sandbox["tools"]["allowed"] == []
