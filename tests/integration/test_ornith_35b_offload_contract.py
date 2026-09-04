from __future__ import annotations

from pathlib import Path

from agent.services.local_model_resource_policy import LocalModelRuntimeProfileLoader

ROOT = Path(__file__).resolve().parents[2]


def test_35b_profile_accounts_for_full_weights_and_no_swap() -> None:
    profile = LocalModelRuntimeProfileLoader().load(ROOT / "config/runtime/ornith-1.5-35b-a3b-64gb-offload.v1.json")

    assert profile.artifact_size_bytes > 20 * 1024**3
    assert profile.minimum_total_ram_bytes == 60 * 1024**3
    assert profile.requires_no_swap_growth is True
    assert profile.maximum_parallel_requests == 1
    assert [item.context_tokens for item in profile.contexts if item.state == "candidate"] == [8192, 16384]
