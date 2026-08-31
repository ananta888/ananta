from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.run_enterprise_organization_release_gate import (
    DEFAULT_TODO,
    GateConfigurationError,
    _run_suite,
    _validate_profile,
    build_report,
    evaluate_task_graph,
)

ROOT = Path(__file__).resolve().parents[1]


def test_release_task_is_the_only_leaf_and_reaches_every_predecessor() -> None:
    todo = json.loads(DEFAULT_TODO.read_text(encoding="utf-8"))

    result = evaluate_task_graph(todo, release_task_id="ESORG-QA-006")

    structural_reasons = {reason for reason in result["reason_codes"] if reason != "release_predecessors_incomplete"}
    assert structural_reasons == set()
    assert result["summary"]["leaves"] == ["ESORG-QA-006"]
    assert result["transitive_predecessor_count"] == result["summary"]["nodes"] - 1


def test_release_profile_has_exactly_one_full_e2e() -> None:
    profile = json.loads(
        (ROOT / "config/test-profiles/enterprise-organizations/release-gate.v1.json").read_text(encoding="utf-8")
    )

    suites = _validate_profile(profile)

    assert sum(suite["tier"] == "full_e2e" for suite in suites) == 1
    e2e = next(suite for suite in suites if suite["tier"] == "full_e2e")
    assert e2e["command"][-1] == "tests/enterprise-organization-medium-eight-team.spec.ts"


def test_profile_rejects_a_second_full_e2e() -> None:
    profile = json.loads(
        (ROOT / "config/test-profiles/enterprise-organizations/release-gate.v1.json").read_text(encoding="utf-8")
    )
    profile["suites"].append({**profile["suites"][-1], "id": "second-e2e"})

    with pytest.raises(GateConfigurationError, match="exactly_one_full_e2e"):
        _validate_profile(profile)


def test_full_gate_runs_without_interactive_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    todo = json.loads(DEFAULT_TODO.read_text(encoding="utf-8"))
    profile_path = ROOT / "config/test-profiles/enterprise-organizations/release-gate.v1.json"
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "scripts.run_enterprise_organization_release_gate._run_suite",
        lambda suite: {**suite, "status": "passed", "reason_code": None},
    )
    monkeypatch.setattr(
        "scripts.run_enterprise_organization_release_gate._hash_inputs",
        lambda *_args: [],
    )

    report, passed = build_report(
        todo=todo,
        profile=profile,
        todo_path=tmp_path / "todo.json",
        profile_path=profile_path,
        mode="full",
    )

    assert passed is True  # The archived TODO and every automated suite are complete.
    assert report["execution_policy"] == {
        "fully_automated": True,
        "interactive_approval_required": False,
    }
    assert {suite["status"] for suite in report["suites"]} == {"passed"}


def test_suite_runner_uses_private_python_bytecode_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object):
        bytecode_dir = Path(str(kwargs["env"]["PYTHONPYCACHEPREFIX"]))
        observed["bytecode_dir"] = bytecode_dir
        observed["command"] = args[0]
        assert bytecode_dir.is_dir()
        return subprocess.CompletedProcess(args[0], 0, b"", b"")

    monkeypatch.setattr(
        "scripts.run_enterprise_organization_release_gate.subprocess.run",
        fake_run,
    )

    result = _run_suite(
        {
            "id": "python-static",
            "tier": "static",
            "cwd": ".",
            "timeout_seconds": 10,
            "command": ["python", "-m", "compileall", "-q", "agent"],
        }
    )

    assert result["status"] == "passed"
    assert observed["command"][:3] == [
        sys.executable,
        "-m",
        "compileall",
    ]
    assert not Path(observed["bytecode_dir"]).exists()


def test_full_e2e_uses_an_isolated_results_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(*_args: object, **kwargs: object):
        results_dir = Path(str(kwargs["env"]["E2E_RESULTS_DIR"]))
        observed["results_dir"] = results_dir
        observed["port"] = str(kwargs["env"]["E2E_PORT"])
        observed["test_timeout_ms"] = str(kwargs["env"]["E2E_TEST_TIMEOUT_MS"])
        assert results_dir.parent.is_dir()
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(
        "scripts.run_enterprise_organization_release_gate.subprocess.run",
        fake_run,
    )

    result = _run_suite(
        {
            "id": "medium-eight-team-e2e",
            "tier": "full_e2e",
            "cwd": "frontend-angular",
            "timeout_seconds": 10,
            "command": ["npx", "playwright", "test", "example.spec.ts"],
        }
    )

    assert result["status"] == "passed"
    assert 0 < int(str(observed["port"])) < 65536
    assert observed["test_timeout_ms"] == "180000"
    assert isinstance(observed["results_dir"], Path)
    assert not observed["results_dir"].parent.exists()


def test_integration_suite_cannot_be_silently_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, str] = {}

    def fake_run(*_args: object, **kwargs: object):
        observed["integration_opt_in"] = str(kwargs["env"].get("RUN_INTEGRATION_TESTS"))
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(
        "scripts.run_enterprise_organization_release_gate.subprocess.run",
        fake_run,
    )

    result = _run_suite(
        {
            "id": "small-runtime-matrix",
            "tier": "complex",
            "cwd": ".",
            "timeout_seconds": 10,
            "command": [
                "python",
                "-m",
                "pytest",
                "-q",
                "tests/integration/test_small_organization_runtime_matrix.py",
            ],
        }
    )

    assert result["status"] == "passed"
    assert observed["integration_opt_in"] == "1"
