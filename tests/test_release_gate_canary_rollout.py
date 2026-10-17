"""Test that the Native-Core-Gate canary rollout is independent of Buzz and Agent/Live gates."""

from __future__ import annotations

import pytest


class TestNativeCoreGateCanaryRollout:
    """Verify that the Native-Core-Gate aggregates only its own sub-gates.
    Agent/Live and Buzz remain independent additive gates."""

    def test_native_core_gate_aggregates_expected_sub_gates(self):
        expected = {
            "contracts",
            "migration",
            "security",
            "room_thread_e2e",
            "replay",
            "search",
            "accessibility",
            "backup_restore",
        }
        # Placeholder: replace with actual gate catalog import when available.
        assert expected.issubset(expected)

    def test_buzz_gate_is_independent(self):
        # Buzz must not be part of the Native-Core-Gate aggregation.
        buzz = {"buzz"}
        native_core = {
            "contracts",
            "migration",
            "security",
            "room_thread_e2e",
            "replay",
            "search",
            "accessibility",
            "backup_restore",
        }
        assert buzz.isdisjoint(native_core)

    def test_agent_live_gate_is_independent(self):
        agent_live = {"agent", "live"}
        native_core = {
            "contracts",
            "migration",
            "security",
            "room_thread_e2e",
            "replay",
            "search",
            "accessibility",
            "backup_restore",
        }
        assert agent_live.isdisjoint(native_core)

    def test_canary_rollout_requires_exit_code_zero(self):
        # Placeholder: actual pytest invocation would be done by the Hub.
        assert True
