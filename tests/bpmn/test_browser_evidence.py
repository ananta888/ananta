"""Real SQL browser reservations reject altered or post-execution observations."""

import copy

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from agent.db_models import TaskDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.db_models.workflow_runtime import WorkflowExecutionOwnershipDB, WorkflowWorkerAssignmentDB
from tests.bpmn_container.browser_evidence import BrowserEvidenceError, SyntheticBrowserEvidence


@pytest.fixture
def browser_evidence(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'browser.sqlite'))
    SQLModel.metadata.create_all(engine)
    clock = [100.0]
    evidence = SyntheticBrowserEvidence(engine=engine, revision='a' * 40, source_digest='b' * 64,
        image_id='sha256:' + 'c' * 64, clock=lambda: clock[0])
    evidence.reserve()
    clock[0] = 101.0
    report = {'passed': True, 'evidence_assignment': copy.deepcopy(evidence.projection),
              'started_at': 100.5, 'finished_at': 101.0, 'workflow_id': 'browser_authenticated_workflow'}
    yield evidence, clock, report
    engine.dispose()


def reserved_state(evidence):
    with Session(evidence.engine) as session:
        return session.exec(select(HubRunEvidenceIdentityDB)).one().state


def test_exact_browser_result_is_pre_reserved_and_synthetic_release_is_denied(browser_evidence):
    evidence, _, report = browser_evidence
    assert reserved_state(evidence) == 'reserved'
    result = evidence.complete(report)
    assert result['state'] == 'succeeded'
    assert result['reserved_before_execution'] and result['production_release_denied']
    assert result['run_id'] == evidence.projection['run_id']
    assert evidence.complete(report) == result


@pytest.mark.parametrize('field,value', [
    ('run_id', 'RUN_unregistered'), ('task_id', 'foreign'), ('assignment_id', 'foreign'),
    ('dispatch_lease_id', 'foreign'), ('source_ids', ['SRC_unregistered']), ('evidence_scope', 'production'),
])
def test_browser_cannot_replace_or_broaden_its_assignment(browser_evidence, field, value):
    evidence, _, report = browser_evidence
    report['evidence_assignment'][field] = value
    with pytest.raises(BrowserEvidenceError, match='assignment_mismatch'):
        evidence.complete(report)
    assert reserved_state(evidence) == 'reserved'


@pytest.mark.parametrize('change', ['expired', 'fence', 'worker', 'project', 'input', 'cancelled'])
def test_current_sql_assignment_lease_and_input_are_required(browser_evidence, change):
    evidence, clock, report = browser_evidence
    if change == 'expired':
        clock[0] = 221
    else:
        with Session(evidence.engine) as session, session.begin():
            if change == 'fence':
                row = session.exec(select(WorkflowExecutionOwnershipDB)).one()
                row.fencing_token += 1
            elif change == 'worker':
                row = session.exec(select(WorkflowWorkerAssignmentDB)).one()
                row.worker_id = 'foreign'
            else:
                row = session.get(TaskDB, evidence.task_id)
                if change == 'project':
                    row.project_id = None
                elif change == 'cancelled':
                    row.status = 'cancelled'
                else:
                    row.worker_execution_context = {**row.worker_execution_context, 'purpose': 'changed'}
            session.add(row)
    with pytest.raises((BrowserEvidenceError, ValueError)):
        evidence.complete(report)
    assert reserved_state(evidence) == 'reserved'


@pytest.mark.parametrize('field,value', [
    ('started_at', 99), ('started_at', True), ('finished_at', 99), ('finished_at', float('nan')),
    ('passed', 'true'), ('passed', False),
])
def test_unknown_or_posthoc_browser_execution_cannot_complete(browser_evidence, field, value):
    evidence, _, report = browser_evidence
    report[field] = value
    with pytest.raises(BrowserEvidenceError):
        evidence.complete(report)
    assert reserved_state(evidence) == 'reserved'
