from __future__ import annotations

from pathlib import Path

from agent.services.local_model_resource_policy import LocalModelRuntimeProfileLoader

ROOT = Path(__file__).resolve().parents[2]


def test_each_profile_has_ollama_and_openai_compatible_runtime_paths() -> None:
    for filename in (
        "ornith-1.5-9b-rtx3080.v1.json",
        "ornith-1.5-35b-a3b-64gb-offload.v1.json",
    ):
        profile = LocalModelRuntimeProfileLoader().load(ROOT / "config/runtime" / filename)
        protocols = {item.protocol for item in profile.runtimes if item.state != "incompatible"}
        assert protocols == {"ollama_native", "openai_compatible"}


def test_optional_servers_preserve_upstream_minimums_and_security_conflict() -> None:
    profile = LocalModelRuntimeProfileLoader().load(
        ROOT / "config/runtime/ornith-1.5-9b-rtx3080.v1.json"
    )
    runtimes = {item.runtime_id: item for item in profile.runtimes}

    assert runtimes["vllm"].minimum_version == "0.19.1"
    assert runtimes["sglang"].minimum_version == "0.5.9"
    assert runtimes["vllm"].state == "incompatible"
    assert runtimes["vllm"].remote_code_allowed is False
    assert "upstream_recipe_requires_remote_code" in runtimes["vllm"].reason_codes


def test_provider_failure_does_not_remove_other_runtime_contracts() -> None:
    profile = LocalModelRuntimeProfileLoader().load(
        ROOT / "config/runtime/ornith-1.5-9b-rtx3080.v1.json"
    )
    available = tuple(item for item in profile.runtimes if item.runtime_id != "ollama")

    assert {item.runtime_id for item in available} == {
        "lmstudio", "llamacpp", "vllm", "sglang"
    }
    assert any(item.protocol == "openai_compatible" for item in available)
