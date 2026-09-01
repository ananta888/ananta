from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from scripts import run_spreadsheet_studio_release_gate as gate


def test_release_profile_exactly_matches_research_matrix() -> None:
    profile = gate.load_profile(gate.DEFAULT_PROFILE)

    assert [item["id"] for item in profile["gates"]] == list(gate._GATE_IDS)
    assert [item["required"] for item in profile["gates"]] == [True, True, True, True, True, False]
    assert all(item["commands"] for item in profile["gates"])

    path = gate.ROOT / "does-not-exist-release-profile.json"
    with pytest.raises(FileNotFoundError):
        gate.load_profile(path)


def test_required_not_run_fails_but_optional_gpu_not_run_is_honest(monkeypatch) -> None:
    profile = gate.load_profile(gate.DEFAULT_PROFILE)
    monkeypatch.setattr(gate, "_git_sha", lambda: "a" * 40)
    monkeypatch.setattr(
        gate,
        "_run_gate",
        lambda item, **_values: {
            "id": item["id"],
            "environment": item["environment"],
            "required": item["required"],
            "status": "passed",
            "reason_code": "spreadsheet_release_gate_passed",
            "commands": [],
        },
    )

    monkeypatch.setattr(
        gate,
        "environment_status",
        lambda item, **_values: (
            (False, "spreadsheet_required_environment_missing")
            if item["id"] == "gpu_lora_smoke"
            else (True, "spreadsheet_release_environment_available")
        ),
    )
    report = gate.run_profile(profile)
    assert report["status"] == "passed"
    assert report["gates"][-1]["status"] == "not_run"
    assert report["human_intervention_required"] is False

    monkeypatch.setattr(
        gate,
        "environment_status",
        lambda item, **_values: (
            (False, "spreadsheet_browser_binary_unavailable")
            if item["id"] == "angular_accessibility_e2e"
            else (True, "spreadsheet_release_environment_available")
        ),
    )
    failed = gate.run_profile(profile)
    assert failed["status"] == "failed"
    assert next(item for item in failed["gates"] if item["id"] == "angular_accessibility_e2e")["status"] == "not_run"


def test_junit_and_browser_evidence_reject_skips(tmp_path) -> None:
    junit = tmp_path / "result.xml"
    root = ET.Element("testsuite", tests="2", failures="0", errors="0", skipped="1")
    ET.ElementTree(root).write(junit)
    result = gate._evidence("junit", junit, tmp_path / "browser.json", tmp_path / "gpu.json")
    assert result["evidence_valid"] is False

    browser = tmp_path / "browser.json"
    browser.write_text(json.dumps({"stats": {"expected": 1, "unexpected": 0, "flaky": 0, "skipped": 0}}))
    result = gate._evidence("playwright_json", junit, browser, tmp_path / "gpu.json")
    assert result["evidence_valid"] is True

    browser.write_text(json.dumps({"stats": {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 1}}))
    assert gate._evidence("playwright_json", junit, browser, tmp_path / "gpu.json")["evidence_valid"] is False


def test_gpu_requires_automatic_inputs_and_never_invents_evidence() -> None:
    profile = gate.load_profile(gate.DEFAULT_PROFILE)
    gpu = profile["gates"][-1]

    available, reason = gate.environment_status(
        gpu,
        environ={"ANANTA_RUN_SPREADSHEET_GPU_GATE": "1"},
        which=lambda _name: "/usr/bin/nvidia-smi",
    )

    assert available is False
    assert reason == "spreadsheet_required_environment_missing"
    assert "ANANTA_UNSLOTH_SRC_IDS" in gpu["required_environment"]
    assert "ANANTA_UNSLOTH_RUN_IDS" in gpu["required_environment"]


def test_empty_environment_does_not_fall_back_to_process_environment(monkeypatch) -> None:
    profile = gate.load_profile(gate.DEFAULT_PROFILE)
    gpu = profile["gates"][-1]
    monkeypatch.setenv("ANANTA_RUN_SPREADSHEET_GPU_GATE", "1")
    for name in gpu["required_environment"]:
        monkeypatch.setenv(name, "configured")

    available, reason = gate.environment_status(
        gpu,
        environ={},
        which=lambda _name: "/usr/bin/nvidia-smi",
    )

    assert available is False
    assert reason == "spreadsheet_required_environment_missing"


def test_release_report_tail_redacts_bearer_and_query_jwts() -> None:
    jwt = "eyJheader000.payload000.signature000"
    output = gate._safe_tail(f"GET /events?token={jwt}\nAuthorization: Bearer {jwt}\n")

    assert jwt not in output
    assert "token=[REDACTED_JWT]" in output
    assert "Authorization: Bearer [REDACTED]" in output


def test_successful_command_evidence_does_not_retain_volatile_logs(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *_args, **_kwargs: gate.subprocess.CompletedProcess([], 0, "timestamp and cid", "warning"),
    )
    definition = {
        "id": "unit_contract_property",
        "environment": "cpu",
        "required": True,
        "timeout_seconds": 30,
        "commands": [{"cwd": ".", "argv": ["true"], "evidence_kind": "exit_code"}],
    }

    result = gate._run_gate(definition, temporary_path=tmp_path, environ={})

    assert result["status"] == "passed"
    assert "stdout_tail" not in result["commands"][0]
    assert "stderr_tail" not in result["commands"][0]
