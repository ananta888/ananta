"""Tests for Job Application Flow — status machine, agents, blueprints (JOBCORE-002/JOBAGENT-001/JOBFLOW-001)."""
from __future__ import annotations

import pytest
from agent.job_module import setup as job_setup
from agent.caseflow.status_machine import get_status_machine
from agent.visual_process.skill_profiles import get_skill_profile_registry
from agent.visual_process.validator import VisualProcessValidator
from agent.job_module.blueprints import JOB_APPLICATION_BLUEPRINTS, register_job_blueprints


@pytest.fixture(autouse=True)
def setup():
    job_setup()


class TestJobStatusMachine:
    def test_job_status_machine_registered(self):
        m = get_status_machine("job_application")
        assert m is not None

    def test_found_to_interesting_valid(self):
        m = get_status_machine("job_application")
        assert m is not None
        result = m.validate_transition("found", "interesting")
        assert result.valid is True

    def test_found_to_offer_invalid(self):
        m = get_status_machine("job_application")
        assert m is not None
        result = m.validate_transition("found", "offer")
        assert result.valid is False
        assert result.error_code == "TRANSITION_NOT_ALLOWED"

    def test_preparing_to_applied_logs_applied_at(self):
        m = get_status_machine("job_application")
        assert m is not None
        result = m.validate_transition("preparing", "applied")
        assert result.valid is True
        assert result.suggested_event_type == "status_changed"

    def test_rejected_is_not_terminal_for_archived(self):
        m = get_status_machine("job_application")
        assert m is not None
        # rejected -> archived: valid
        result = m.validate_transition("rejected", "archived")
        assert result.valid is True

    def test_archived_is_terminal(self):
        m = get_status_machine("job_application")
        assert m is not None
        result = m.validate_transition("archived", "found")
        assert result.valid is False
        assert result.error_code == "TERMINAL_STATUS"


class TestJobSkillProfiles:
    def test_job_skill_profiles_registered(self):
        registry = get_skill_profile_registry()
        profile_ids = [p.id for p in registry.all()]
        expected = [
            "job_discovery_agent", "job_posting_parser_agent",
            "cover_letter_agent", "fit_evaluator_agent",
            "application_audit_agent",
        ]
        for pid in expected:
            assert pid in profile_ids, f"Profile '{pid}' not registered"

    def test_cover_letter_agent_cannot_send_email(self):
        registry = get_skill_profile_registry()
        profile = registry.get("cover_letter_agent")
        assert profile is not None
        assert "send_email" in profile.forbidden_tools
        assert "send_message" in profile.forbidden_tools

    def test_audit_agent_read_only(self):
        registry = get_skill_profile_registry()
        profile = registry.get("application_audit_agent")
        assert profile is not None
        assert "read_only" in profile.capabilities
        assert "write_artifact" not in profile.allowed_tools


class TestJobBlueprints:
    def test_blueprints_are_valid_visual_process_graphs(self):
        validator = VisualProcessValidator()
        for bp_id, graph in JOB_APPLICATION_BLUEPRINTS.items():
            result = validator.validate(graph)
            assert result.valid, f"Blueprint {bp_id} invalid: {result.errors}"

    def test_blueprints_have_human_gate_steps(self):
        gate_blueprints = [
            "preset-job-application-intake",
            "preset-job-discovery-to-case",
            "preset-cover-letter-generation",
            "preset-followup",
        ]
        for bp_id in gate_blueprints:
            graph = JOB_APPLICATION_BLUEPRINTS.get(bp_id)
            assert graph is not None, f"Blueprint {bp_id} not registered"
            has_gate = any(s.gate for s in graph.steps)
            assert has_gate, f"Blueprint {bp_id} has no gate step"
