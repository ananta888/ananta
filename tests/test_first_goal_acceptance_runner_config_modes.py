import importlib.util
import sys
from pathlib import Path


def _load_runner_module():
    runner_path = Path(__file__).resolve().parents[1] / "scripts" / "first_goal_acceptance_runner.py"
    spec = importlib.util.spec_from_file_location("first_goal_acceptance_runner", runner_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeRunner:
    instances = []

    def __init__(self, *args, **kwargs):
        self.patches = []
        _FakeRunner.instances.append(self)

    def get_config(self):
        return {"default_provider": "ollama", "default_model": "ananta-default:latest"}

    def set_config_patch(self, patch):
        self.patches.append(dict(patch or {}))

    def restart_autopilot_unscoped(self):
        return None


def test_goal_scoped_mode_does_not_patch_config(monkeypatch, tmp_path):
    mod = _load_runner_module()
    monkeypatch.setattr(mod, "AcceptanceRunner", _FakeRunner)
    monkeypatch.setattr(mod, "run_once", lambda *args, **kwargs: mod.RunReport(run_index=1, final_goal_status="completed"))
    monkeypatch.setattr(mod, "aggregate", lambda reports: {"repeatability_pass": True})

    out = tmp_path / "report.json"
    import sys

    argv = [
        "runner",
        "--config-mode",
        "goal_scoped",
        "--runs",
        "1",
        "--out",
        str(out),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    exit_code = mod.main()
    assert exit_code == 0
    runner = _FakeRunner.instances[-1]
    assert runner.patches == []


def test_legacy_mode_applies_config_patch(monkeypatch, tmp_path):
    mod = _load_runner_module()
    _FakeRunner.instances.clear()
    monkeypatch.setattr(mod, "AcceptanceRunner", _FakeRunner)
    monkeypatch.setattr(mod, "run_once", lambda *args, **kwargs: mod.RunReport(run_index=1, final_goal_status="completed"))
    monkeypatch.setattr(mod, "aggregate", lambda reports: {"repeatability_pass": True})

    import sys

    out = tmp_path / "report.json"
    argv = ["runner", "--config-mode", "legacy_global_config", "--runs", "1", "--out", str(out)]
    monkeypatch.setattr(sys, "argv", argv)
    exit_code = mod.main()
    assert exit_code == 0
    runner = _FakeRunner.instances[-1]
    assert len(runner.patches) >= 1


def test_parallel_legacy_blocked_without_allow_flag(monkeypatch, tmp_path):
    mod = _load_runner_module()
    monkeypatch.setattr(mod, "AcceptanceRunner", _FakeRunner)

    import sys

    out = tmp_path / "report.json"
    argv = [
        "runner",
        "--config-mode",
        "legacy_global_config",
        "--parallel-goals-per-scenario",
        "2",
        "--runs",
        "1",
        "--out",
        str(out),
    ]
    monkeypatch.setattr(sys, "argv", argv)
    try:
        mod.main()
        raised = False
    except SystemExit as exc:
        raised = True
        assert "parallel mode is blocked" in str(exc)
    assert raised is True
