from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Sequence

import pytest
import yaml

from scripts.run_training_backend_image_gate import (
    BACKENDS,
    DockerImageGate,
    ImageGateError,
    evaluate_scan,
    load_scanner_config,
)


class RecordingRunner:
    def __init__(self, inspect_result: dict) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.inspect_result = inspect_result

    def run(self, argv: Sequence[str], *, timeout_seconds: int) -> str:
        del timeout_seconds
        command = tuple(argv)
        self.commands.append(command)
        if command[:3] == ("docker", "image", "inspect"):
            return json.dumps([self.inspect_result])
        return ""


def _sbom(license_name: str = "Apache-2.0") -> dict:
    return {
        "artifacts": [
            {
                "name": "trainer",
                "version": "1.0",
                "licenses": [{"spdxExpression": license_name}],
            }
        ]
    }


def _scanner(severity: str | None = None) -> dict:
    matches = []
    if severity is not None:
        matches.append({"vulnerability": {"id": "CVE-fixture", "severity": severity}})
    return {"matches": matches}


def _policy() -> dict[str, int]:
    return {"maximum_critical": 0, "maximum_high": 0, "maximum_unresolved_licenses": 0}


def test_pinned_scanner_configuration_is_closed_and_fail_closed() -> None:
    config = load_scanner_config()
    assert config["syft"]["image"].count("@sha256:") == 1
    assert config["grype"]["image"].count("@sha256:") == 1
    assert config["policy"] == _policy()


def test_clean_scan_passes_without_human_approval() -> None:
    result = evaluate_scan(_sbom(), _scanner(), _policy())
    assert result["status"] == "passed"
    assert result["reason_codes"] == []
    assert result["package_count"] == 1


@pytest.mark.parametrize(
    ("license_name", "severity", "reason"),
    [
        ("Apache-2.0", "Critical", "critical_vulnerability"),
        ("Apache-2.0", "High", "high_vulnerability"),
        ("NOASSERTION", None, "unresolved_dependency_license"),
    ],
)
def test_release_blockers_are_machine_decided(license_name: str, severity: str | None, reason: str) -> None:
    result = evaluate_scan(_sbom(license_name), _scanner(severity), _policy())
    assert result["status"] == "failed"
    assert reason in result["reason_codes"]


def test_invalid_scanner_contract_cannot_be_reinterpreted_as_success() -> None:
    malformed = copy.deepcopy(_scanner())
    malformed["matches"] = ["not-an-object"]
    with pytest.raises(ImageGateError, match="vulnerability_finding_invalid"):
        evaluate_scan(_sbom(), malformed, _policy())


def test_install_smoke_is_bounded_netless_and_shell_free() -> None:
    runner = RecordingRunner(
        {
            "Id": "sha256:" + ("a" * 64),
            "Size": 1,
            "Config": {
                "User": "10005:10005",
                "Env": [
                    "HF_DATASETS_OFFLINE=1",
                    "HF_HUB_DISABLE_TELEMETRY=1",
                    "HF_HUB_OFFLINE=1",
                    "TRANSFORMERS_OFFLINE=1",
                ],
            },
        }
    )

    result = DockerImageGate(runner).inspect_and_smoke(BACKENDS[0])

    assert result["install_smoke"] == "verified"
    run_commands = [command for command in runner.commands if command[:2] == ("docker", "run")]
    assert len(run_commands) == 2
    for command in run_commands:
        assert ("--network", "none") == command[command.index("--network") : command.index("--network") + 2]
        assert "--read-only" in command
        assert ("--cap-drop", "ALL") == command[command.index("--cap-drop") : command.index("--cap-drop") + 2]
        assert "/bin/sh" not in command
        assert "bash" not in command


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("User", "root", "container_non_root_invalid"),
        ("Env", [], "container_offline_environment_invalid"),
    ],
)
def test_container_contract_fails_closed(field: str, value: object, reason: str) -> None:
    inspect_result = {
        "Id": "sha256:" + ("a" * 64),
        "Size": 1,
        "Config": {
            "User": "10005:10005",
            "Env": [
                "HF_DATASETS_OFFLINE=1",
                "HF_HUB_DISABLE_TELEMETRY=1",
                "HF_HUB_OFFLINE=1",
                "TRANSFORMERS_OFFLINE=1",
            ],
        },
    }
    inspect_result["Config"][field] = value
    runner = RecordingRunner(inspect_result)

    with pytest.raises(ImageGateError, match=reason):
        DockerImageGate(runner).inspect_and_smoke(BACKENDS[0])


def test_build_uses_explicit_dockerfile_and_no_shell() -> None:
    runner = RecordingRunner({})

    DockerImageGate(runner).build(BACKENDS[0])

    command = runner.commands[0]
    assert command[:3] == ("docker", "build", "--progress=plain")
    assert Path(command[command.index("--file") + 1]).name == "Dockerfile.training-axolotl"
    assert "/bin/sh" not in command


def test_scanners_run_as_calling_user_and_leave_readable_reports(tmp_path: Path) -> None:
    report_root = tmp_path / "reports"
    cache_root = tmp_path / "cache"
    report_root.mkdir()
    cache_root.mkdir()
    (report_root / "axolotl.sbom.json").write_text(json.dumps(_sbom()), encoding="utf-8")
    (report_root / "axolotl.grype.json").write_text(json.dumps(_scanner()), encoding="utf-8")
    runner = RecordingRunner({})
    scanners = load_scanner_config()

    DockerImageGate(runner).scan(BACKENDS[0], scanners, report_root, cache_root)

    assert len(runner.commands) == 2
    for command in runner.commands:
        assert "--user" in command
        assert command[command.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
        assert ("--env", "HOME=/tmp") == command[command.index("--env") : command.index("--env") + 2]


def test_nvidia_prerequisite_has_automatic_triggers_and_no_required_human_input() -> None:
    workflow_path = Path("docs/ci/training-backends-nvidia-acceptance.workflow.yml")
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    triggers = workflow[True]

    assert {"schedule", "push"}.issubset(triggers)
    assert triggers["workflow_dispatch"] is None
    run_step = workflow["jobs"]["automatic-image-prerequisite"]["steps"][2]
    assert "--build" in run_step["run"]
    assert "--scan" in run_step["run"]
