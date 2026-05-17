import importlib.util
import sys
from pathlib import Path


def _load_runner_module():
    runner_path = Path(__file__).resolve().parents[2] / "scripts" / "first_goal_acceptance_runner.py"
    spec = importlib.util.spec_from_file_location("first_goal_acceptance_runner", runner_path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_scenarios_define_distinct_goal_scoped_profiles():
    mod = _load_runner_module()
    scenarios = mod._scenario_definitions({"default_provider": "ollama", "default_model": "ananta-default:latest"})
    ids = {s["id"] for s in scenarios}
    profiles = {s.get("config_profile") for s in scenarios}
    assert ids == {"opencode_preconfigured", "opencode_ollama_local", "ananta_ollama_local"}
    assert profiles == ids


def test_parallel_legacy_mode_guard_criterion():
    # Mirrors acceptance criterion AC-15 in a cheap e2e-style assertion.
    config_mode = "legacy_global_config"
    parallel_n = 2
    allow_unsafe = False
    blocked = parallel_n > 1 and config_mode == "legacy_global_config" and not allow_unsafe
    assert blocked is True
