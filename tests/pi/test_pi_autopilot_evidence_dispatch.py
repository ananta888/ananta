"""Actual Hub dispatch route and SQL Registry; synthetic Worker transport."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.routes.tasks.autopilot_native_dispatch import (
    NATIVE_DISPATCH_PORTS,
    NativeDispatchPorts,
    dispatch_native_task,
)
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.native_result_task_projection import native_task_command
from tests.pi.test_pi_dispatch_transaction import assigned, preparation
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_result_completion_policy import complete
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401


@pytest.mark.parametrize('mode', ['success', 'installed', 'missing', 'completion_failure', 'journal_failure'])
def test_pi_route_requires_atomic_preparation_and_registry_completion(reservation, mode):
    app = Flask(__name__)
    app.config['ROLE'] = 'hub'
    repo = reservation.admission.repository
    service = preparation(reservation)
    if mode == 'installed':
        from agent.services.pi_dispatch_runtime import install_pi_dispatch_service

        install_pi_dispatch_service(app, service=service)
    worker = SimpleNamespace(role='worker', url=reservation.values['manifest'].worker_url, token='synthetic-token')
    transport = Mock(return_value=reservation.admission.response)
    journal = SQLAlchemyNativeDispatchJournal(reservation.admission.engine)
    if mode == 'journal_failure':
        journal.settle = Mock(side_effect=ValueError('synthetic_journal_unavailable'))
        journal.reconcile = Mock(side_effect=ValueError('synthetic_journal_unavailable'))

    def close(**values):
        if mode == 'completion_failure':
            raise ValueError('synthetic_registry_unavailable')
        return service.complete(**values)

    app.extensions[NATIVE_DISPATCH_PORTS.extension_key] = NativeDispatchPorts(
        command=native_task_command, assignment=lambda **kwargs: assigned(reservation),
        signing_keys=Mock(side_effect=AssertionError('legacy signing must never run')),
        transport=lambda: SimpleNamespace(forward_task=transport),
        accept_result=lambda **kwargs: complete(reservation.admission),
        journal=lambda: journal,
        prepare_pi=None if mode in {'missing', 'installed'} else service.prepare,
        complete_pi=None if mode == 'installed' else close)

    def update(task_id, status, **values):
        task = repo.get_by_id(task_id)
        task.status = status
        for name, value in values.items():
            setattr(task, name, value)
        repo.save(task)

    ctx = SimpleNamespace(task=repo.get_by_id(reservation.admission.task_id), target_worker=worker,
        app_ctx=app, dependencies=SimpleNamespace(repository_registry=lambda app: SimpleNamespace(task_repo=repo),
            current_task_status=lambda task_id, **kwargs: repo.get_by_id(task_id).status),
        update_local_task_status=update, append_trace_event=Mock())
    result = dispatch_native_task(ctx, policy={'execute_timeout': 30})
    with Session(reservation.admission.engine) as session:
        runs = session.exec(select(HubRunEvidenceIdentityDB)).all()
        journals = session.exec(select(NativeDispatchDB)).all()
    if mode in {'success', 'installed'}:
        assert result.completed and not result.failed
        assert len(runs) == 1 and runs[0].state == 'succeeded'
        assert len(journals) == 1 and journals[0].state == 'accepted'
        assert transport.call_args.args[2]['evidence_assignment']['run_id'] == runs[0].run_id
    elif mode == 'missing':
        assert result.failed and not result.completed
        transport.assert_not_called()
        assert runs == [] and journals == []
    else:
        assert result.failed and not result.completed
        assert repo.get_by_id(ctx.task.id).status == 'completed'
        assert runs[0].state == ('succeeded' if mode == 'journal_failure' else 'reserved')
        assert journals[0].state == ('sending' if mode == 'journal_failure' else 'unknown')
