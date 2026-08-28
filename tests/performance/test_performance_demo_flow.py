import os
import sys
from pathlib import Path

from agent.services.patch_sandbox_service import PatchSandboxService
from agent.services.regression_gate_service import RegressionGateService

FIXTURE = Path(__file__).parents[1] / "fixtures" / "performance_demo"


def _expose_current_python(monkeypatch) -> None:
    current_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{Path(sys.executable).parent}{os.pathsep}{current_path}")


def test_performance_demo_candidate_regression_passes(monkeypatch):
    _expose_current_python(monkeypatch)
    patch = (FIXTURE / "candidate.patch").read_text(encoding="utf-8")
    sandbox = PatchSandboxService().create_sandbox(workspace_dir=FIXTURE, patch_text=patch)
    assert sandbox["status"] == "completed"
    regression = RegressionGateService().evaluate(
        workspace_dir=sandbox["sandbox_dir"],
        test_commands=["pytest -q test_slow_math.py"],
    )
    assert regression["status"] == "candidate_passed"


def test_performance_demo_bad_candidate_rejected(monkeypatch):
    _expose_current_python(monkeypatch)
    patch = (FIXTURE / "bad_candidate.patch").read_text(encoding="utf-8")
    sandbox = PatchSandboxService().create_sandbox(workspace_dir=FIXTURE, patch_text=patch)
    assert sandbox["status"] == "completed"
    regression = RegressionGateService().evaluate(
        workspace_dir=sandbox["sandbox_dir"],
        test_commands=["pytest -q test_slow_math.py"],
    )
    assert regression["status"] == "rejected"
