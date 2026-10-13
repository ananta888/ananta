"""Actual Hub issuance, signed Worker execution and durable Hub acknowledgement.

Release and outer scope are explicit synthetic policies; no production evidence.
"""

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.db_models import AgentInfoDB
from agent.db_models.native_result_retirement import NativeResultRetirementManifestDB, NativeResultRetirementMarkerDB
from agent.db_models.workflow_runtime import WorkflowExecutionAttemptHistoryDB, WorkflowExecutionOwnershipDB
from agent.routes.tasks.autopilot_native_dispatch import (
    NATIVE_DISPATCH_PORTS,
    NativeDispatchPorts,
    dispatch_native_task,
)
from agent.services.native_result_maintenance_policy import configured_maintenance_schedules
from agent.services.native_result_maintenance_runtime import RegisteredMaintenanceAdmission
from agent.services.native_result_task_projection import native_task_command
from ananta_contracts.native_result_retirement import RETIREMENT_TOOL
from tests.bpmn.test_native_task_execution_http import signed as signed
from tests.bpmn.test_result_maintenance_cadence_integration import integrated as integrated


def prepare_second_run(fixture):
    assert fixture.cadence().run_once()['active'] == 1
    first = fixture.execute()
    assert fixture.cadence().run_once()['completed'] == 1
    fixture.now[0] += 400
    with Session(fixture.engine) as session, session.begin():
        worker = session.get(AgentInfoDB, fixture.schedule.owner['worker_url'])
        worker.last_seen = fixture.now[0]
        session.add(worker)
    fixture.config[0]['schedules'][0]['retire_expired'] = True
    fixture.admission.require(configured_maintenance_schedules(fixture.config[0])[0])
    outcome = fixture.cadence().run_once()
    assert outcome['active'] == 1, outcome
    second, = [task for task in fixture.children() if task.id != first.id]
    return first, second


def test_hub_permit_worker_compaction_and_accepted_receipt_confirm_one_slot(integrated):  # noqa: F811
    first, second = prepare_second_run(integrated)
    accepted = integrated.execute(second.id)
    output = accepted.verification_status['native_node_result']['output_data']
    assert output['compacted_results'] == 1
    assert [entry['task_id'] for entry in output['retired_entries']] == [first.id]
    with Session(integrated.engine) as session:
        manifest = session.get(NativeResultRetirementManifestDB, second.id)
        assert manifest.state == 'confirmed'
        marker, = session.exec(select(NativeResultRetirementMarkerDB)).all()
        assert marker.task_id == first.id and marker.maintenance_task_id == second.id
        assert marker.binding_digest == output['retired_entries'][0]['binding_digest']
    with sqlite3.connect(integrated.store.path) as db:
        assert db.execute('SELECT count(*) FROM native_results WHERE task_id=?', (first.id,)).fetchone()[0] == 0
        marker_count, = db.execute('SELECT count(*) FROM native_result_tombstones WHERE task_id=?',
                                  (first.id,)).fetchone()
        assert marker_count == 1
        assert db.execute('SELECT count(*) FROM native_result_maintenance_reservations').fetchone()[0] == 1
    assert integrated.retirement.confirm(task_id=second.id) == 1


def test_crash_after_accepted_receipt_before_acknowledgement_reconciles_same_manifest(integrated):  # noqa: F811
    first, second = prepare_second_run(integrated)
    integrated.execute(second.id, confirm_retirement=False)
    with Session(integrated.engine) as session:
        assert session.get(NativeResultRetirementManifestDB, second.id).state == 'issued'
        assert not session.exec(select(NativeResultRetirementMarkerDB)).all()
    assert integrated.retirement.reconcile(tenant_id=first.tenant_id, project_id=first.project_id) == 1
    assert integrated.retirement.reconcile(tenant_id=first.tenant_id, project_id=first.project_id) == 0
    with Session(integrated.engine) as session:
        assert session.get(NativeResultRetirementManifestDB, second.id).state == 'confirmed'


def test_actual_autopilot_retirement_branch_persists_manifest_before_dispatch_and_confirms_receipt(
        integrated, monkeypatch):  # noqa: F811
    first, second = prepare_second_run(integrated)
    worker = integrated.agents.get_by_url(integrated.schedule.owner['worker_url'])
    worker = SimpleNamespace(**{**worker.model_dump(), 'token': 'x' * 40})
    app = Flask(__name__)
    app.config['ROLE'] = 'hub'
    registry = SimpleNamespace(task_repo=integrated.tasks, agent_repo=integrated.agents)
    preparation_errors = []

    def prepare(**values):
        try:
            return integrated.retirement.prepare(**values)
        except Exception as exc:
            preparation_errors.append(str(exc))
            raise

    def assign(**values):
        return integrated.assignment.bind_dispatched_task(task=values['task'], worker=values['worker'])

    def update(task_id, status, **values):
        task = integrated.tasks.get_by_id(task_id)
        task.status = status
        for key, value in values.items():
            setattr(task, key, value)
        integrated.tasks.save(task)

    def transport(url, endpoint, payload, **values):
        with Session(integrated.engine) as session:
            assert session.get(NativeResultRetirementManifestDB, second.id).manifest == payload['task'][
                'worker_execution_context']['native_result_retirement_manifest']
        response = integrated.app.test_client().post(endpoint, json=payload,
            headers={'Authorization': 'Bearer ' + values['token']})
        assert response.status_code == 200, response.json
        return response.json

    def accept(**values):
        response = values['response']
        outer = response['workflow_adapter_verification']['workflow_adapter_task_result']
        task = integrated.tasks.get_by_id(values['tid'])
        task.status, task.last_output = response['status'], response['output']
        task.last_exit_code = response['exit_code']
        task.verification_status = {'workflow_adapter_task_result': outer,
            'native_node_result': outer['adapter_result']['verification']['native_node_result']}
        integrated.tasks.save(task)

    app.extensions[NATIVE_DISPATCH_PORTS.extension_key] = NativeDispatchPorts(command=native_task_command,
        assignment=assign, signing_keys=lambda: integrated.keys,
        transport=lambda: SimpleNamespace(forward_task=transport), accept_result=accept,
        journal=lambda: integrated.journal,
        retirement=SimpleNamespace(prepare=prepare, confirm=integrated.retirement.confirm))
    monkeypatch.setattr('agent.routes.tasks.autopilot_native_dispatch.time.time', lambda: integrated.now[0])
    ctx = SimpleNamespace(task=second, target_worker=worker, app_ctx=app,
        dependencies=SimpleNamespace(repository_registry=lambda _: registry,
            current_task_status=lambda task_id, **_: integrated.tasks.get_by_id(task_id).status),
        update_local_task_status=update, append_trace_event=lambda *args, **kwargs: None)
    result = dispatch_native_task(ctx, policy={'execute_timeout': 30})
    assert result.completed and not result.failed, (second.status, preparation_errors)
    with Session(integrated.engine) as session:
        assert session.get(NativeResultRetirementManifestDB, second.id).state == 'confirmed'
        marker, = session.exec(select(NativeResultRetirementMarkerDB)).all()
        assert marker.task_id == first.id


@pytest.mark.parametrize('case', ['policy_withdrawn', 'revoked_grant', 'wrong_assignment', 'dispatch_already_reserved'])
def test_unavailable_current_authority_never_issues_or_persists_a_permit(integrated, case):  # noqa: F811
    _, second = prepare_second_run(integrated)
    assigned = integrated.assignment.bind_dispatched_task(task=second,
        worker=integrated.agents.get_by_url(integrated.schedule.owner['worker_url']))
    if case == 'policy_withdrawn':
        integrated.config[0]['enabled'] = False
    elif case == 'revoked_grant':
        integrated.grants.revoke(native_task_command(second).authorization.envelope_id,
                                 reason_code='synthetic_policy_withdrawal')
    elif case == 'wrong_assignment':
        assigned = replace(assigned, revision=assigned.revision + 1)
    else:
        integrated.journal.begin(task=second, assignment=assigned)
    with pytest.raises((ValueError, RuntimeError)):
        integrated.retirement.prepare(task_id=second.id, assignment=assigned)
    with Session(integrated.engine) as session:
        assert session.get(NativeResultRetirementManifestDB, second.id) is None
    with sqlite3.connect(integrated.store.path) as db:
        assert db.execute('SELECT count(*) FROM native_result_tombstones').fetchone()[0] == 0


def test_retirement_cannot_bypass_mandatory_human_tool_policy(integrated):  # noqa: F811
    prepare_second_run(integrated)
    schedule, = configured_maintenance_schedules(integrated.config[0])
    admission = RegisteredMaintenanceAdmission(engine=integrated.engine,
        registry=lambda: SimpleNamespace(task_repo=integrated.tasks, agent_repo=integrated.agents),
        release=SimpleNamespace(evaluate=lambda **_: (True, 'synthetic_fixture')),
        governance=lambda: {'approval_lifecycle': {'human_required_tools': [RETIREMENT_TOOL]}},
        clock=lambda: integrated.now[0])
    with pytest.raises(PermissionError, match='mandatory_approval_required'):
        admission.require(schedule)


def test_mutated_journal_is_blocked_without_creating_confirmed_markers(integrated):  # noqa: F811
    _, second = prepare_second_run(integrated)
    integrated.execute(second.id, confirm_retirement=False)
    with Session(integrated.engine) as session, session.begin():
        manifest = session.get(NativeResultRetirementManifestDB, second.id)
        manifest.manifest_digest = '0' * 64
        session.add(manifest)
    assert integrated.retirement.reconcile() == 0
    with Session(integrated.engine) as session:
        assert session.get(NativeResultRetirementManifestDB, second.id).state == 'blocked'
        assert not session.exec(select(NativeResultRetirementMarkerDB)).all()


@pytest.mark.parametrize('history', ['valid', 'missing', 'mutated'])
def test_superseded_lease_retirement_uses_exact_original_hub_history(integrated, history):  # noqa: F811
    from agent.services.native_result_retirement_admission import require_retirable_result

    first, second = prepare_second_run(integrated)
    command = native_task_command(first)
    with Session(integrated.engine) as session, session.begin():
        row = session.exec(select(WorkflowExecutionOwnershipDB).where(
            WorkflowExecutionOwnershipDB.run_id == command.run_id)).one()
        snapshot = dict(row.ownership)
        updates = {'attempt_id': 'synthetic-successor', 'fencing_token': row.fencing_token + 1,
            'revision': row.revision + 1, 'status': 'active',
            'lease_expires_at': float(integrated.now[0] + 300), 'last_heartbeat_at': float(integrated.now[0])}
        for field, value in updates.items():
            setattr(row, field, value)
        row.ownership = {**snapshot, **updates, 'result_ack_key': '', 'failure_code': ''}
        session.add(row)
        if history != 'valid':
            revision = first.verification_status['native_result_receipt']['authority']['ownership_revision']
            original = session.exec(select(WorkflowExecutionAttemptHistoryDB).where(
                WorkflowExecutionAttemptHistoryDB.run_id == command.run_id,
                WorkflowExecutionAttemptHistoryDB.revision == revision)).one()
            if history == 'missing':
                session.delete(original)
            else:
                original.attempt_id = 'synthetic-mutated-attempt'
                session.add(original)
    if history != 'valid':
        with pytest.raises((ValueError, RuntimeError)):
            with Session(integrated.engine) as session, session.begin():
                require_retirable_result(session=session, task_id=first.id, tenant_id=first.tenant_id,
                    project_id=first.project_id, owner=integrated.schedule.owner, now=integrated.now[0])
        accepted = integrated.execute(second.id)
        assert accepted.verification_status['native_node_result']['output_data']['compacted_results'] == 0
        return
    with Session(integrated.engine) as session, session.begin():
        require_retirable_result(session=session, task_id=first.id, tenant_id=first.tenant_id,
            project_id=first.project_id, owner=integrated.schedule.owner, now=integrated.now[0])
    accepted = integrated.execute(second.id)
    assert accepted.verification_status['native_node_result']['output_data']['compacted_results'] == 1
