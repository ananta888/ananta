"""Tests for CaseFlow Status Machine (CASECORE-002)."""
from __future__ import annotations

import pytest
from agent.caseflow.status_machine import (
    CaseStatusDefinition,
    TransitionResult,
    get_status_machine,
    register_status_machine,
)
from agent.job_module import setup as job_setup


@pytest.fixture(autouse=True)
def setup_job_module():
    job_setup()


class TestCaseStatusMachine:
    def _make_machine(self):
        return CaseStatusDefinition(
            statuses=["new", "active", "done", "archived"],
            initial_status="new",
            terminal_statuses=["done", "archived"],
            transitions={
                "new": ["active", "archived"],
                "active": ["done", "archived"],
            },
        )

    def test_valid_transition_returns_true(self):
        m = self._make_machine()
        result = m.validate_transition("new", "active")
        assert result.valid is True

    def test_invalid_transition_returns_false_with_error_code(self):
        m = self._make_machine()
        result = m.validate_transition("new", "done")
        assert result.valid is False
        assert result.error_code == "TRANSITION_NOT_ALLOWED"

    def test_terminal_status_cannot_be_left(self):
        m = self._make_machine()
        result = m.validate_transition("done", "active")
        assert result.valid is False
        assert result.error_code == "TERMINAL_STATUS"

    def test_unknown_from_status_error(self):
        m = self._make_machine()
        result = m.validate_transition("nonexistent", "active")
        assert result.valid is False
        assert result.error_code == "UNKNOWN_FROM_STATUS"

    def test_unknown_to_status_error(self):
        m = self._make_machine()
        result = m.validate_transition("new", "nonexistent")
        assert result.valid is False
        assert result.error_code == "UNKNOWN_TO_STATUS"

    def test_transition_result_has_suggested_event_type(self):
        m = self._make_machine()
        result = m.validate_transition("new", "active")
        assert result.valid is True
        assert result.suggested_event_type == "status_changed"

    def test_job_application_transition_found_to_interesting(self):
        m = get_status_machine("job_application")
        assert m is not None
        result = m.validate_transition("found", "interesting")
        assert result.valid is True

    def test_job_application_transition_found_to_offer_forbidden(self):
        m = get_status_machine("job_application")
        assert m is not None
        result = m.validate_transition("found", "offer")
        assert result.valid is False
        assert result.error_code == "TRANSITION_NOT_ALLOWED"

    def test_job_application_rejected_is_terminal_until_archived(self):
        m = get_status_machine("job_application")
        assert m is not None
        # rejected -> archived is allowed
        result = m.validate_transition("rejected", "archived")
        assert result.valid is True
        # archived is terminal
        result = m.validate_transition("archived", "found")
        assert result.valid is False
