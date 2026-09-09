"""Worker-safe target DTO and backwards-compatible public CLI facade."""

import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ananta_contracts.coding_agent_target import CodingAgentInferenceTarget


def target(**changes):
    return CodingAgentInferenceTarget(
        **{
            "client_id": "pi",
            "provider_id": "ollama",
            "model": "synthetic-model",
            "cli_model": "synthetic-model",
            "base_url": "http://model.invalid/v1",
            "target_kind": "local_openai",
            "api_key": "synthetic-private-key",
            "api_key_source": "synthetic-policy",
            **changes,
        }
    )


def test_existing_cli_import_reexports_the_identical_contract():
    from agent.cli_backends.coding_agent_targets import CodingAgentInferenceTarget as ExistingTarget

    assert ExistingTarget is CodingAgentInferenceTarget
    with pytest.raises(FrozenInstanceError):
        target().model = "different"


def test_public_metadata_and_explicit_environment_keep_the_existing_contract():
    value = target()
    assert value.public_metadata() == {
        "client_id": "pi",
        "target_provider": "ollama",
        "target_model": "synthetic-model",
        "cli_model": "synthetic-model",
        "target_base_url": "http://model.invalid/v1",
        "target_kind": "local_openai",
        "target_provider_type": None,
        "api_key_configured": True,
        "api_key_source": "synthetic-policy",
    }
    assert value.api_key not in str(value.public_metadata())
    assert value.process_environment() == {
        "OPENAI_API_BASE": value.base_url,
        "OPENAI_BASE_URL": value.base_url,
        "OPENAI_API_KEY": value.api_key,
    }
    assert target(base_url=None, api_key=None).process_environment() == {}


def test_worker_contract_import_does_not_load_agent_configuration():
    root = Path(__file__).resolve().parents[1]
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from ananta_contracts.coding_agent_target import CodingAgentInferenceTarget
assert not any(name == 'agent' or name.startswith('agent.') for name in sys.modules)
assert CodingAgentInferenceTarget.__module__ == 'ananta_contracts.coding_agent_target'
print('worker-target-contract-ok')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(root)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip() == "worker-target-contract-ok"
