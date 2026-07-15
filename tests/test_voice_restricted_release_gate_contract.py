from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scripts.run_voice_restricted_release_gates import (
    load_gate,
    require_hardware_environment,
    select_commands,
    select_groups,
    select_nodes,
)

ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = ROOT / "config" / "release-gates" / "voice-restricted-core.v1.json"
PROFILES_PATH = ROOT / "config" / "release-gates" / "voice-restricted-hardware-profiles.v1.json"
HARDWARE_GATE_PATH = ROOT / "tests" / "e2e" / "test_voice_restricted_compose_hardware_gate.py"
RUNBOOK_PATH = ROOT / "docs" / "operations" / "voice-restricted-production-runbook.md"
PYTEST_CONFTEST_PATH = ROOT / "tests" / "conftest.py"


def test_core_release_gate_has_non_hardware_security_coverage_and_existing_nodes() -> None:
    gate = json.loads(GATE_PATH.read_text(encoding="utf-8"))
    assert gate["schema_version"] == "ananta.release-gate.v1"
    groups = gate["groups"]
    required_groups = {
        "architecture",
        "security",
        "contracts",
        "fusion-golden",
        "evaluation",
        "compose-contract",
        "angular",
        "angular-compose",
        "optional-capabilities",
        "hardware-compose",
    }
    assert set(groups) == required_groups
    assert groups["security"]["hardware"] is False
    assert set(groups["security"]["coverage"]) == {
        "audio_and_file_attacks",
        "model_supply_chain",
        "ssrf_and_dns_rebinding",
        "remote_code_and_pickle",
        "envelope_manipulation_and_no_generation",
        "local_judge_network_boundary",
        "consent_and_tenant_bypass",
        "privacy_delete_and_idempotency",
        "runtime_cleanup_restart_replay",
    }
    for group_id, group in groups.items():
        assert group["pytest_nodes"], group_id
        for node in group["pytest_nodes"]:
            assert (ROOT / node.split("::", 1)[0]).is_file(), node
    assert all(group["hardware"] is False for name, group in groups.items() if name != "hardware-compose")
    assert groups["optional-capabilities"]["core"] is False


def test_release_gate_runner_separates_core_from_hardware_and_deduplicates_nodes() -> None:
    gate = load_gate(GATE_PATH)
    core_groups = select_groups(gate, "core")

    assert "hardware-compose" not in core_groups
    assert "optional-capabilities" not in core_groups
    assert all(gate["groups"][name]["hardware"] is False for name in core_groups)
    nodes = select_nodes(gate, core_groups)
    assert len(nodes) == len(set(nodes))
    assert "tests/test_voice_fusion_golden.py" in nodes
    assert select_commands(gate, core_groups) == (
        (ROOT / "frontend-angular", ("npm", "run", "test:unit")),
        (
            ROOT / "frontend-angular",
            ("npm", "run", "build", "--", "--output-path", "{gate_tmp}/angular-build"),
        ),
    )


def test_hardware_gate_refuses_implicit_or_incomplete_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = load_gate(GATE_PATH)
    group = select_groups(gate, "hardware-compose")
    for variable in gate["groups"]["hardware-compose"]["required_environment"]:
        monkeypatch.delenv(variable, raising=False)

    with pytest.raises(ValueError, match="explicit environment"):
        require_hardware_environment(gate, group)

    for variable in gate["groups"]["hardware-compose"]["required_environment"]:
        monkeypatch.setenv(variable, "1" if variable == "ANANTA_RUN_VOICE_RESTRICTED_HARDWARE" else os.devnull)
    require_hardware_environment(gate, group)


def test_hardware_gate_requires_real_models_strategies_and_controlled_admission_inputs() -> None:
    gate = load_gate(GATE_PATH)
    required = set(gate["groups"]["hardware-compose"]["required_environment"])
    assert {
        "ANANTA_VOICE_E2E_PRIMARY_BACKEND",
        "ANANTA_VOICE_E2E_SECONDARY_BACKEND",
        "ANANTA_RESTRICTED_INFERENCE_MANIFEST_SCORE_CHOICES",
        "ANANTA_RESTRICTED_INFERENCE_ADMISSION_DENIED_MANIFEST_ID",
        "ANANTA_RESTRICTED_INFERENCE_ADMISSION_REASON",
        "ANANTA_RESTRICTED_INFERENCE_MAX_RAM_BYTES",
        "ANANTA_RESTRICTED_INFERENCE_MAX_VRAM_BYTES",
        "HUB_PORT",
        "VOICE_WHISPER_CPP_PROMPT_MAX_CHARS",
    }.issubset(required)

    source = HARDWARE_GATE_PATH.read_text(encoding="utf-8")
    for required_contract in (
        "score_choices",
        "no_generation",
        "parallel_compare",
        "classic_then_correct",
        "worker_unavailable",
        "ram_budget_exhausted",
        "vram_budget_exhausted",
    ):
        assert required_contract in source
    assert "MemoryError" not in source
    assert '"down", "--remove-orphans", "--volumes"' in source

    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")
    for variable in required:
        assert variable in runbook
    assert "allocation-based OOM" in runbook

    conftest = PYTEST_CONFTEST_PATH.read_text(encoding="utf-8")
    assert 'os.environ.setdefault("INITIAL_ADMIN_PASSWORD", "admin")' in conftest


def test_optional_capability_gate_is_explicit_and_does_not_block_core() -> None:
    gate = load_gate(GATE_PATH)
    optional_groups = select_groups(gate, "optional-capabilities")
    assert optional_groups == ("optional-capabilities",)
    optional_nodes = select_nodes(gate, optional_groups)
    assert "tests/test_generative_judge_worker_contract.py" in optional_nodes
    assert "tests/services/test_generative_judge_worker_port.py" in optional_nodes
    assert "tests/worker/test_generative_judge_app.py" in optional_nodes
    assert "tests/test_voice_corrector_worker_contract.py" in optional_nodes
    assert "tests/services/test_generative_corrector_worker_port.py" in optional_nodes
    assert "tests/services/test_voice_generative_corrector_service.py" in optional_nodes
    assert "tests/test_voice_generative_corrector_routes.py" in optional_nodes
    assert "tests/worker/test_generative_corrector_app.py" in optional_nodes
    assert "tests/test_voice_runtime_optional_extensions.py" in optional_nodes
    assert "tests/test_voice_runtime_streaming_api.py" in optional_nodes


def test_hardware_profiles_are_versioned_bounded_and_require_real_evidence() -> None:
    payload = json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "ananta.voice-restricted-hardware-profiles.v1"
    profiles = {item["profile_id"]: item for item in payload["profiles"]}
    assert set(profiles) == {"cpu", "rtx-3080", "high-end-gpu"}
    assert profiles["cpu"]["compose_profile"] == "voice-production-cpu"
    assert profiles["rtx-3080"]["required_hardware"]["vram_mb_min"] == 10240
    assert profiles["high-end-gpu"]["required_hardware"]["vram_mb_min"] >= 24576
    assert profiles["cpu"]["voice_backends"] == ["vosk", "whisper_cpp"]
    for profile_id in ("rtx-3080", "high-end-gpu"):
        assert profiles[profile_id]["voice_backends"] == ["faster_whisper", "vosk", "whisper_cpp"]
    for profile in profiles.values():
        assert profile["resource_environment"]
        assert {
            "quality",
            "calibration",
            "latency",
            "peak_ram",
            "resource_admission",
            "worker_recovery",
            "restricted_no_generation",
            "fusion_strategies",
            "no_network",
            "provenance",
        }.issubset(
            profile["evidence_required"]
        )
        resource_environment = profile["resource_environment"]
        assert "ANANTA_RESTRICTED_INFERENCE_MAX_RAM_BYTES" in resource_environment
        assert "ANANTA_RESTRICTED_INFERENCE_MAX_VRAM_BYTES" in resource_environment
        assert resource_environment["VOICE_WHISPER_CPP_PROMPT_MAX_CHARS"] == "512"
        assert set(profile["voice_backends"]).issubset(profile["intended_capabilities"])
