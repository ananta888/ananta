import pytest

from agent.bootstrap.startup import run_startup_phase


def test_run_startup_phase_returns_action_result():
    assert run_startup_phase("unit_phase", lambda value: value + 1, 41) == 42


def test_run_startup_phase_reraises_failures():
    with pytest.raises(RuntimeError, match="boom"):
        run_startup_phase("failing_unit_phase", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

