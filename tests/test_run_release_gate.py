from __future__ import annotations

import subprocess
import sys

import scripts.run_release_gate as run_release_gate


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["cmd"], returncode=returncode, stdout="", stderr="")


def test_run_release_gate_runs_release_gate_then_e2e(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: True)

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--strict"])

    assert run_release_gate.main() == 0
    assert commands[0] == ["python3", "scripts/release_gate.py", "--strict"]
    assert commands[1][0:2] == ["python3", "scripts/run_security_invariant_checks.py"]
    assert commands[2][0:2] == ["python3", "scripts/audit_domain_integrations.py"]
    assert commands[3][0:2] == ["python3", "scripts/run_e2e_dogfood_checks.py"]


def test_run_release_gate_stops_when_release_gate_fails(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: True)

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        if any(str(part).endswith("release_gate.py") for part in command):
            return _completed(1)
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py"])

    assert run_release_gate.main() == 1
    assert len(commands) == 1


def test_run_release_gate_can_skip_domain_audit(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: True)

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--skip-domain-audit", "--skip-e2e"])

    assert run_release_gate.main() == 0
    assert commands[0] == ["python3", "scripts/release_gate.py"]
    assert commands[1][0:2] == ["python3", "scripts/run_security_invariant_checks.py"]
    assert len(commands) == 2


def test_run_release_gate_stops_when_security_invariants_fail(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: True)

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        if any(str(part).endswith("run_security_invariant_checks.py") for part in command):
            return _completed(1)
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py"])

    assert run_release_gate.main() == 1
    assert commands[0] == ["python3", "scripts/release_gate.py"]
    assert commands[1][0:2] == ["python3", "scripts/run_security_invariant_checks.py"]
    assert len(commands) == 2


def test_run_release_gate_runs_worker_checks_when_runtime_claimed(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: False)

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--worker-runtime-claimed", "--skip-e2e"])

    assert run_release_gate.main() == 0
    assert commands[0] == ["python3", "scripts/release_gate.py"]
    assert commands[1][0:2] == ["python3", "scripts/run_security_invariant_checks.py"]
    assert commands[2][0:2] == ["python3", "scripts/run_worker_checks.py"]


def test_run_release_gate_runs_cli_smoke_when_runtime_claimed(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: False)

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--cli-runtime-claimed", "--skip-e2e"])

    assert run_release_gate.main() == 0
    assert commands[0] == ["python3", "scripts/release_gate.py"]
    assert commands[1][0:2] == ["python3", "scripts/run_security_invariant_checks.py"]
    assert commands[2] == ["python3", "-m", "pytest", "-q", "tests/smoke/test_unified_cli_smoke.py"]


def test_run_release_gate_stops_when_cli_smoke_fails(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: False)

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        if command[0:3] == ["python3", "-m", "pytest"]:
            return _completed(1)
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--cli-runtime-claimed", "--skip-e2e"])

    assert run_release_gate.main() == 1
    assert commands[2] == ["python3", "-m", "pytest", "-q", "tests/smoke/test_unified_cli_smoke.py"]


def test_run_release_gate_runs_tdd_smoke_when_runtime_claimed(monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: False)
    monkeypatch.setattr(
        run_release_gate,
        "_evaluate_tdd_smoke_report",
        lambda _path: (True, "ok", ["artifacts/tdd/red.json", "artifacts/tdd/green.json"]),
    )

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        commands.append(list(command))
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--tdd-runtime-claimed", "--skip-e2e"])

    assert run_release_gate.main() == 0
    assert commands[0] == ["python3", "scripts/release_gate.py"]
    assert commands[1][0:2] == ["python3", "scripts/run_security_invariant_checks.py"]
    assert commands[2][0:2] == ["python3", "scripts/run_tdd_blueprint_smoke.py"]


def test_run_release_gate_fails_when_green_claimed_without_passing_tdd_evidence(monkeypatch) -> None:
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: False)
    monkeypatch.setattr(
        run_release_gate,
        "_evaluate_tdd_smoke_report",
        lambda _path: (False, "green_phase_claimed_without_passing_test_evidence", ["artifacts/tdd/red.json"]),
    )

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--tdd-runtime-claimed", "--skip-e2e"])

    assert run_release_gate.main() == 1


def test_run_release_gate_fails_when_red_skipped_without_degraded_explanation(monkeypatch) -> None:
    monkeypatch.setattr(run_release_gate, "_python_executable", lambda: "python3")
    monkeypatch.setattr(run_release_gate, "_domain_inventory_exists", lambda _path: False)
    monkeypatch.setattr(
        run_release_gate,
        "_evaluate_tdd_smoke_report",
        lambda _path: (False, "red_phase_skipped_without_degraded_explanation", ["artifacts/tdd/green.json"]),
    )

    def fake_run(command, cwd=None, check=False):  # noqa: ANN001
        return _completed(0)

    monkeypatch.setattr(run_release_gate.subprocess, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["run_release_gate.py", "--tdd-runtime-claimed", "--skip-e2e"])

    assert run_release_gate.main() == 1
