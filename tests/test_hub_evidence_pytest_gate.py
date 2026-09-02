from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from sqlmodel import Session, create_engine, select

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from scripts import run_hub_evidence_pytest_gate as runner

ROOT = Path(__file__).resolve().parents[1]
PROFILES = sorted((ROOT / "config/release-gates/hub-evidence").glob("*.json"))


@pytest.mark.parametrize("profile_path", PROFILES, ids=lambda path: path.stem)
def test_profiles_are_closed_and_reference_existing_sources(profile_path: Path) -> None:
    profile = runner.load_profile(profile_path)

    assert profile["source_paths"]
    assert profile["pytest_args"]
    assert profile["evidence_scope"] == profile["required_scope"]


def test_profile_rejects_command_override_and_path_escape(tmp_path: Path) -> None:
    profile = json.loads(PROFILES[0].read_text(encoding="utf-8"))
    profile["pytest_args"] = ["-c", "/tmp/hostile.ini"]
    source = tmp_path / "source.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    profile["source_paths"] = ["source.py"]
    candidate = tmp_path / "invalid-profile.json"
    candidate.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(
        runner.HubEvidencePytestGateError,
        match="hub_evidence_gate_pytest_args_invalid",
    ):
        runner.load_profile(candidate, root=tmp_path)

    profile["pytest_args"] = ["-q", "tests/test_hub_evidence_pytest_gate.py"]
    profile["source_paths"] = ["../outside.py"]
    candidate.write_text(json.dumps(profile), encoding="utf-8")
    with pytest.raises(
        runner.HubEvidencePytestGateError,
        match="hub_evidence_gate_path_invalid",
    ):
        runner.load_profile(candidate, root=tmp_path)


def test_runner_reserves_before_pytest_and_verifies_exact_result(tmp_path: Path, monkeypatch) -> None:
    profile_path = PROFILES[0]
    profile = runner.load_profile(profile_path)
    profile = {
        **profile,
        "pytest_args": ["-q", "tests/test_hub_evidence_pytest_gate.py"],
        "minimum_tests": 3,
    }
    observed_assignment: dict = {}

    def fake_run(argv, **kwargs):
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, stdout="1" * 40 + "\n", stderr="")
        junit_argument = next(value for value in argv if value.startswith("--junitxml="))
        Path(junit_argument.split("=", 1)[1]).write_text(
            '<testsuite tests="3" failures="0" errors="0" skipped="0"/>',
            encoding="utf-8",
        )
        observed_assignment.update(json.loads(kwargs["env"]["ANANTA_HUB_EVIDENCE_ASSIGNMENT"]))
        return subprocess.CompletedProcess(argv, 0, stdout="3 passed\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    output = tmp_path / "report.json"
    junit = tmp_path / "junit.xml"
    database_path = tmp_path / "evidence.sqlite3"

    report, returncode = runner.run_profile(
        profile,
        profile_path=profile_path,
        output_path=output,
        database_url=f"sqlite:///{database_path}",
        junit_path=junit,
        require_clean=False,
    )

    assert returncode == 0
    assert report["passed"] is True
    assert report["verified"] is True
    assert report["human_intervention_required"] is False
    assert observed_assignment["run_id"] == report["run_id"]
    with Session(create_engine(f"sqlite:///{database_path}")) as session:
        row = session.exec(select(HubRunEvidenceIdentityDB)).one()
    assert row.state == "succeeded"
    assert row.run_id == report["run_id"]


def test_skipped_tests_fail_closed_and_terminalize_run(tmp_path: Path, monkeypatch) -> None:
    profile_path = PROFILES[0]
    profile = {
        **runner.load_profile(profile_path),
        "pytest_args": ["-q", "tests/test_hub_evidence_pytest_gate.py"],
        "minimum_tests": 1,
    }

    def fake_run(argv, **_kwargs):
        if argv[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 0, stdout="2" * 40 + "\n", stderr="")
        junit_argument = next(value for value in argv if value.startswith("--junitxml="))
        Path(junit_argument.split("=", 1)[1]).write_text(
            '<testsuite tests="1" failures="0" errors="0" skipped="1"/>',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(argv, 0, stdout="1 skipped\n", stderr="")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    database_path = tmp_path / "evidence.sqlite3"
    report, returncode = runner.run_profile(
        profile,
        profile_path=profile_path,
        output_path=tmp_path / "report.json",
        database_url=f"sqlite:///{database_path}",
        junit_path=tmp_path / "junit.xml",
        require_clean=False,
    )

    assert returncode == 1
    assert report["passed"] is False
    assert report["verified"] is False
    assert report["reason_code"] == "evidence_run_not_successful"


def test_sqlite_registry_parent_is_created_for_fresh_checkout(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "runtime" / "evidence.sqlite3"

    value = runner._prepare_database(f"sqlite:///{database_path}")

    assert value == f"sqlite:///{database_path}"
    assert database_path.parent.is_dir()
