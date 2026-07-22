import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_runtime_limit_profile_stays_unsupported_without_host_evidence():
    profile = json.loads((ROOT / "config/sfu/sfu-broadcast-runtime-limits.json").read_text())

    assert profile["activation_status"] == "unsupported"
    assert profile["preflight"]["evidence_refs"] == []
    assert profile["host"]["packet_rate_limit_required"] is True
    assert profile["host"]["open_egress_allowed"] is False


def test_all_sfu_runtime_containers_have_hard_process_limits():
    compose = yaml.safe_load((ROOT / "docker-compose.sfu-broadcast.yml").read_text())
    names = (
        "sfu-broadcast-livekit-native-a",
        "sfu-broadcast-livekit-native-b",
        "sfu-broadcast-livekit",
        "sfu-runtime-agent",
        "sfu-runtime-agent-a",
        "sfu-runtime-agent-b",
    )
    for name in names:
        service = compose["services"][name]
        assert service["read_only"] is True
        assert service["user"] == "65532:65532"
        assert service["cap_drop"] == ["ALL"]
        assert service["memswap_limit"] == service["mem_limit"]
        assert service["pids_limit"] > 0
        assert service["ulimits"]["core"] == {"soft": 0, "hard": 0}


def test_preflight_names_kernel_enforcement_instead_of_claiming_compose_proof():
    script = (ROOT / "scripts/sfu-broadcast-runtime-preflight.sh").read_text()

    assert "nft list ruleset" in script
    assert "bpftool prog show" in script
    assert "cgroup.controllers" in script
    assert 'status=unsupported' in script

