from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.run_enterprise_organization_release_gate import (
    DEFAULT_TODO,
    GateConfigurationError,
    _run_suite,
    _validate_profile,
    evaluate_task_graph,
)

ROOT = Path(__file__).resolve().parents[1]


def test_release_task_is_the_only_leaf_and_reaches_every_predecessor() -> None:
    todo = json.loads(
        DEFAULT_TODO.read_text(encoding="utf-8")
    )

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


def test_suite_runner_uses_private_python_bytecode_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(*args: object, **kwargs: object):
        bytecode_dir = Path(str(kwargs["env"]["PYTHONPYCACHEPREFIX"]))
        observed["bytecode_dir"] = bytecode_dir
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
    assert not Path(observed["bytecode_dir"]).exists()
