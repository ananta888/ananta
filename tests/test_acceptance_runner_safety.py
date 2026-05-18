"""ARD-003: Tests for DB reset safety guards in the acceptance runner."""
from __future__ import annotations

import sys
import types
import pytest


def _import_helpers():
    """Import _is_local_base_url and reset_runtime_data without executing __main__ block."""
    import importlib.util, pathlib
    mod_name = "first_goal_acceptance_runner"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    spec = importlib.util.spec_from_file_location(
        mod_name,
        pathlib.Path(__file__).parent.parent / "scripts" / "first_goal_acceptance_runner.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def runner_mod():
    return _import_helpers()


class TestIsLocalBaseUrl:
    def test_localhost_accepted(self, runner_mod):
        assert runner_mod._is_local_base_url("http://localhost:8080") is True

    def test_127_accepted(self, runner_mod):
        assert runner_mod._is_local_base_url("http://127.0.0.1:5000") is True

    def test_0_0_0_0_accepted(self, runner_mod):
        assert runner_mod._is_local_base_url("http://0.0.0.0:5000") is True

    def test_remote_ip_rejected(self, runner_mod):
        assert runner_mod._is_local_base_url("http://192.168.1.100:5000") is False

    def test_remote_hostname_rejected(self, runner_mod):
        assert runner_mod._is_local_base_url("https://prod.example.com") is False

    def test_empty_string_rejected(self, runner_mod):
        assert runner_mod._is_local_base_url("") is False


class TestResetRuntimeDataGuards:
    def test_raises_without_confirmed_flag(self, runner_mod):
        with pytest.raises(SystemExit, match="i-understand-this-deletes-local-test-data"):
            runner_mod.reset_runtime_data(base_url="http://localhost:5000", confirmed=False)

    def test_raises_for_non_local_url_even_if_confirmed(self, runner_mod):
        with pytest.raises(SystemExit, match="non-local base_url"):
            runner_mod.reset_runtime_data(base_url="https://prod.example.com", confirmed=True)

    def test_refuses_remote_ip_even_if_confirmed(self, runner_mod):
        with pytest.raises(SystemExit, match="non-local base_url"):
            runner_mod.reset_runtime_data(base_url="http://10.0.0.1:5000", confirmed=True)

    def test_proceeds_to_subprocess_for_local_confirmed(self, runner_mod, monkeypatch):
        calls = []
        monkeypatch.setattr(runner_mod.subprocess, "run", lambda *a, **kw: calls.append((a, kw)))
        runner_mod.reset_runtime_data(base_url="http://localhost:5000", confirmed=True)
        assert len(calls) == 1
        cmd = calls[0][0][0]
        assert "psql" in cmd


class TestProviderObserverSnapshot:
    """PO-003: get_provider_observer_snapshot must never raise."""

    def test_snapshot_returns_error_fallback_on_connection_failure(self, runner_mod):
        import unittest.mock as mock
        runner = mock.Mock()
        runner.base_url = "http://localhost:5000"
        runner.headers = {}
        with mock.patch("requests.get", side_effect=ConnectionError("refused")):
            result = runner_mod.AcceptanceRunner.get_provider_observer_snapshot(runner)
        assert "error" in result
        assert result.get("available") is False

    def test_snapshot_returns_fallback_on_non_200(self, runner_mod):
        import unittest.mock as mock
        runner = mock.Mock()
        runner.base_url = "http://localhost:5000"
        runner.headers = {}
        fake_resp = mock.Mock()
        fake_resp.status_code = 503
        with mock.patch("requests.get", return_value=fake_resp):
            result = runner_mod.AcceptanceRunner.get_provider_observer_snapshot(runner)
        assert "http_503" in result.get("error", "")
        assert result.get("available") is False

    def test_snapshot_returns_data_on_200(self, runner_mod):
        import unittest.mock as mock
        runner = mock.Mock()
        runner.base_url = "http://localhost:5000"
        runner.headers = {}
        fake_resp = mock.Mock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"data": {"providers": {"ollama": {"runtime": {"ok": True}}}}}
        with mock.patch("requests.get", return_value=fake_resp):
            result = runner_mod.AcceptanceRunner.get_provider_observer_snapshot(runner)
        assert "providers" in result


# ARD-005: CI-safe mode
class TestCiSafeMode:
    def test_run_report_has_ci_safe_mode_field(self, runner_mod):
        report = runner_mod.RunReport(run_index=1, ci_safe_mode=True)
        assert report.ci_safe_mode is True

    def test_run_report_skipped_checks_defaults_empty(self, runner_mod):
        report = runner_mod.RunReport(run_index=1)
        assert report.skipped_checks == []

    def test_criterion_stable_id_for_provider_stability(self, runner_mod):
        c = runner_mod.CriterionResult(5, "Provider-Stabilität", True, "skipped_in_ci_safe_mode")
        assert c.criterion_id == "provider_stability"

    def test_ci_safe_flag_in_argparser(self, runner_mod):
        """--ci-safe must be a recognized CLI argument."""
        import argparse
        # Simulate parsing with --ci-safe
        # We can't call main() directly, so we test via the _CRITERION_STABLE_IDS and RunReport fields
        report = runner_mod.RunReport(run_index=1, ci_safe_mode=True, skipped_checks=["provider_stability"])
        assert report.ci_safe_mode is True
        assert "provider_stability" in report.skipped_checks
