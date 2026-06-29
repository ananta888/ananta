"""Tests for Job Trace Integrity (JOBAGENT-002)."""
from __future__ import annotations

import uuid
import pytest
from datetime import datetime
from agent.caseflow.agent_runs import AgentRunStatus, CaseAgentRun
from agent.caseflow.artifacts import CaseArtifact
from agent.caseflow.timeline import CaseEvent, append_event, get_events_for_case, clear_events
from agent.job_module import setup as job_setup


@pytest.fixture(autouse=True)
def setup():
    job_setup()
    clear_events()
    yield
    clear_events()


class TestTraceIntegrity:
    def test_agent_run_has_trace_id(self):
        trace_id = str(uuid.uuid4())
        run = CaseAgentRun(
            case_id="case-1",
            agent_profile_id="fit_evaluator_agent",
            trace_id=trace_id,
        )
        assert run.trace_id == trace_id
        assert run.status == AgentRunStatus.running

    def test_generated_artifact_references_agent_run(self):
        run = CaseAgentRun(
            case_id="case-1",
            agent_profile_id="cover_letter_agent",
            trace_id="trace-abc",
        )
        artifact = CaseArtifact(
            case_id="case-1",
            artifact_type="cover_letter",
            title="Generated Cover Letter",
            source="agent",
            trace_id=run.trace_id,
            agent_run_id=run.id,
        )
        assert artifact.agent_run_id == run.id
        assert artifact.trace_id == "trace-abc"
        assert artifact.source == "agent"

    def test_failed_agent_run_stored(self):
        run = CaseAgentRun(
            case_id="case-1",
            agent_profile_id="fit_evaluator_agent",
            trace_id="trace-fail",
        )
        run.status = AgentRunStatus.error
        run.error_code = "LLM_TIMEOUT"
        run.error_detail = "LLM did not respond within timeout"
        run.finished_at = datetime.utcnow()
        assert run.status == AgentRunStatus.error
        assert run.error_code == "LLM_TIMEOUT"
        assert run.finished_at is not None

    def test_fit_evaluator_run_creates_events(self):
        case_id = "case-1"
        run = CaseAgentRun(
            case_id=case_id,
            agent_profile_id="fit_evaluator_agent",
            trace_id="trace-eval-1",
        )
        # Simulate: agent_run_started event
        start_event = CaseEvent(
            case_id=case_id,
            event_type="agent_run_started",
            title="FitEvaluator started",
            payload={"agent_profile_id": "fit_evaluator_agent"},
            trace_id=run.trace_id,
        )
        append_event(case_id, start_event)
        run.status = AgentRunStatus.done
        run.finished_at = datetime.utcnow()
        done_event = CaseEvent(
            case_id=case_id,
            event_type="agent_run_completed",
            title="FitEvaluator completed",
            payload={"agent_profile_id": "fit_evaluator_agent", "status": "done"},
            trace_id=run.trace_id,
        )
        append_event(case_id, done_event)
        events = get_events_for_case(case_id)
        assert len(events) == 2
        assert events[0].event_type == "agent_run_started"
        assert events[1].event_type == "agent_run_completed"
        assert all(e.trace_id == "trace-eval-1" for e in events)

    def test_trace_id_findable_from_detail_view(self):
        case_id = "case-detail"
        trace_id = "trace-findable-xyz"
        run = CaseAgentRun(
            case_id=case_id,
            agent_profile_id="cover_letter_agent",
            trace_id=trace_id,
        )
        artifact = CaseArtifact(
            case_id=case_id,
            artifact_type="cover_letter",
            title="CL Draft",
            source="agent",
            trace_id=trace_id,
            agent_run_id=run.id,
        )
        event = CaseEvent(
            case_id=case_id,
            event_type="artifact_added",
            title="Cover Letter added",
            trace_id=trace_id,
            artifact_id=artifact.id,
            payload={"artifact_type": "cover_letter"},
        )
        append_event(case_id, event)
        events = get_events_for_case(case_id)
        # Find trace via artifact or event
        found_trace = next(
            (e.trace_id for e in events if e.artifact_id == artifact.id),
            None
        )
        assert found_trace == trace_id
