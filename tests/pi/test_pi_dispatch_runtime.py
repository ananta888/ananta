"""Per-application Hub composition and real persisted Pi recovery."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.native_dispatch_result_recovery import (
    production_native_result_recovery_ports,
    recover_native_dispatch_results,
)
from agent.services.pi_dispatch_runtime import get_pi_dispatch_service, install_pi_dispatch_service
from tests.pi.test_pi_dispatch_transaction import assigned, preparation
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_result_completion_policy import complete
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401


def hub():
    app = Flask(__name__)
    app.config['ROLE'] = 'hub'
    return app


def test_composition_is_per_app_and_cannot_be_installed_on_worker(reservation):
    first, second = hub(), hub()
    service = preparation(reservation)
    install_pi_dispatch_service(first, service=service)
    assert get_pi_dispatch_service(first) is service
    assert get_pi_dispatch_service(second) is None
    with pytest.raises(ValueError, match='already_installed'):
        install_pi_dispatch_service(first, service=service)
    second.config['ROLE'] = 'worker'
    with pytest.raises(ValueError, match='hub_required'):
        install_pi_dispatch_service(second, service=service)
    with pytest.raises(ValueError, match='composition_invalid'):
        install_pi_dispatch_service(hub(), service=SimpleNamespace())


def test_production_recovery_factory_uses_installed_durable_service(reservation, monkeypatch):
    from sqlalchemy import create_engine

    from agent.services import native_dispatch_journal, repository_registry, workflow_worker_assignment_runtime

    preparation(reservation).prepare(task_id=reservation.admission.task_id, assignment=assigned(reservation))
    complete(reservation.admission)
    engine = create_engine(reservation.admission.engine.url)
    try:
        app = hub()
        reopened = preparation(reservation, engine=engine)
        install_pi_dispatch_service(app, service=reopened)
        monkeypatch.setattr(repository_registry, 'get_repository_registry',
            lambda: SimpleNamespace(task_repo=reservation.admission.repository, agent_repo=Mock()))
        monkeypatch.setattr(native_dispatch_journal, 'production_native_dispatch_journal',
            lambda: SQLAlchemyNativeDispatchJournal(engine))
        monkeypatch.setattr(workflow_worker_assignment_runtime, 'get_workflow_worker_assignment_store', lambda: Mock())
        with app.app_context():
            ports = production_native_result_recovery_ports()
            assert ports.pi_assignment == reopened.reserved_assignment
            assert ports.complete_pi == reopened.complete_reserved
            transport = Mock(side_effect=AssertionError('must not send or fetch terminal recovery'))
            monkeypatch.setattr(ports.transport, 'forward_task', transport)
            result = recover_native_dispatch_results(ports=ports, tenant_id=reservation.values['manifest'].tenant_id)
        assert result['accepted'] == 1 and result['unresolved'] == 0
        transport.assert_not_called()
        with Session(engine) as session:
            assert session.exec(select(HubRunEvidenceIdentityDB)).one().state == 'succeeded'
            assert session.exec(select(NativeDispatchDB)).one().state == 'accepted'
    finally:
        engine.dispose()
