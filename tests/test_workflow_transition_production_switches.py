"""The opt-in switches that put the transition and trace paths into production.

Both default to off. They are new behaviour on the Hub's most safety-critical
path, so a deployment has to ask for them rather than inherit them from an
upgrade — and a switch that silently did nothing would be worse than no switch.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services.workflow_control_production_composition import (
    ANANTA_COMMAND_TRANSITIONS_ENV,
    ANANTA_TERMINAL_TRACE_ENV,
    production_command_transition_runtime,
    production_terminal_trace_runtime,
)


class _StatusReads:
    def get_workflow_status(self, workflow_id: str) -> dict[str, Any]:
        del workflow_id
        return {"status": "running", "revision": 1, "checkpoint_ref": "c-1"}


def test_both_paths_are_off_without_an_explicit_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ANANTA_TERMINAL_TRACE_ENV, raising=False)
    monkeypatch.delenv(ANANTA_COMMAND_TRANSITIONS_ENV, raising=False)

    assert production_terminal_trace_runtime() is None
    assert production_command_transition_runtime(_StatusReads()) is None


@pytest.mark.parametrize("value", ("0", "false", "no", "off", "", "maybe"))
def test_an_unrecognised_value_leaves_the_path_off(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Anything but an explicit yes means no."""

    monkeypatch.setenv(ANANTA_TERMINAL_TRACE_ENV, value)
    monkeypatch.setenv(ANANTA_COMMAND_TRANSITIONS_ENV, value)

    assert production_terminal_trace_runtime() is None
    assert production_command_transition_runtime(_StatusReads()) is None


@pytest.mark.parametrize("value", ("1", "true", "TRUE", "yes", "on"))
def test_an_explicit_yes_builds_the_terminal_trace_path(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(ANANTA_TERMINAL_TRACE_ENV, value)

    runtime = production_terminal_trace_runtime()

    assert runtime is not None
    assert callable(runtime.state.mark_pending)
    assert callable(runtime.reconciler.drain)


@pytest.mark.parametrize("value", ("1", "true", "yes", "on"))
def test_an_explicit_yes_builds_the_command_transition_path(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv(ANANTA_COMMAND_TRANSITIONS_ENV, value)

    runtime = production_command_transition_runtime(_StatusReads())

    assert runtime is not None
    assert callable(runtime.admission.stage_or_adopt)
    assert callable(runtime.driver.tick)


def test_the_two_switches_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The staged rollout depends on trace being enablable on its own."""

    monkeypatch.setenv(ANANTA_TERMINAL_TRACE_ENV, "1")
    monkeypatch.delenv(ANANTA_COMMAND_TRANSITIONS_ENV, raising=False)

    assert production_terminal_trace_runtime() is not None
    assert production_command_transition_runtime(_StatusReads()) is None


def test_each_command_transition_runtime_claims_its_own_owner_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two Hub processes must not share a runner identity, or they fence each other."""

    monkeypatch.setenv(ANANTA_COMMAND_TRANSITIONS_ENV, "1")

    first = production_command_transition_runtime(_StatusReads())
    second = production_command_transition_runtime(_StatusReads())

    assert first is not None
    assert second is not None
    assert first.driver is not second.driver
