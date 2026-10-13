"""The TUI consumes the same real SQL-backed Hub readmodel as Angular."""

import json
from unittest.mock import Mock

import pytest

from client_surfaces.operator_tui._renderer_content import _content_lines
from client_surfaces.operator_tui.commands import execute_command
from client_surfaces.operator_tui.models import OperatorMode, OperatorState
from tests.security.test_memory_audit_operator import operator as operator  # noqa: F401
from tests.security.test_memory_snapshots import snapshots as snapshots  # noqa: F401
from tests.test_memory_policy_repository import persisted as persisted  # noqa: F401


def test_command_dispatch_reads_actual_hub_project_audit_without_worker_or_content_access(operator, monkeypatch):
    _, _, _, _, app = operator
    calls = []

    class HubHttpFixture:
        def __init__(self, endpoint, *, token):
            assert endpoint == 'http://synthetic-hub' and token == 'synthetic-operator-token'

        def request_json(self, method, path):
            calls.append((method, path))
            response = app.test_client().get(path, headers={'Authorization': 'Bearer synthetic-operator-token'})
            assert response.status_code == 200
            return response.json

    monkeypatch.setattr('client_surfaces.operator_tui.commands_memory_audit.OpsApiClient', HubHttpFixture)
    state = OperatorState(endpoint='http://synthetic-hub', mode=OperatorMode.COMMAND,
                          audit_context={'token': 'synthetic-operator-token'})
    result = execute_command(':memory-audit project-a --run-id run-1 --page-size 1', state)
    assert result.handled and result.state.section_id == 'audit'
    viewer = result.state.header_logo_game['audit_viewer']
    payload = json.loads(viewer['text'])
    assert payload['items'][0]['run_id'] == 'run-1'
    assert payload['next_after_sequence'] == 1
    assert viewer['active'] is True
    assert calls == [('GET', '/api/projects/project-a/memory-audit/events?run_id=run-1&page_size=1')]
    assert 'synthetic-operator-token' not in viewer['text']
    assert result.state.mode is OperatorMode.NORMAL
    assert 'Memory-Zugriffe' in '\n'.join(_content_lines(result.state, 100, height=40))


@pytest.mark.parametrize('command', [
    ':memory-audit project-a --tenant-id foreign', ':memory-audit project-a --run-id',
    ':memory-audit project-a --run-id one --run-id two', ':memory-audit project-a --scope invalid',
    ':memory-audit project-a --after-sequence -1', ':memory-audit project-a --page-size 201',
])
def test_invalid_options_never_contact_hub(command, monkeypatch):
    client = Mock(side_effect=AssertionError('unexpected network access'))
    monkeypatch.setattr('client_surfaces.operator_tui.commands_memory_audit.OpsApiClient', client)
    result = execute_command(command, OperatorState(endpoint='http://synthetic-hub'))
    assert not result.handled
    client.assert_not_called()


def test_backend_failure_never_exposes_secret_error_details(monkeypatch):
    client = Mock()
    client.return_value.request_json.side_effect = RuntimeError('synthetic-private-token-and-database-error')
    monkeypatch.setattr('client_surfaces.operator_tui.commands_memory_audit.OpsApiClient', client)
    result = execute_command(':memory-audit project-a', OperatorState(endpoint='http://synthetic-hub'))
    assert not result.handled and 'synthetic-private-token' not in result.message


def test_unbound_response_is_not_displayed(monkeypatch):
    client = Mock()
    client.return_value.request_json.return_value = {'data': {'project_id': 'foreign-project'}}
    monkeypatch.setattr('client_surfaces.operator_tui.commands_memory_audit.OpsApiClient', client)
    state = OperatorState(endpoint='http://synthetic-hub')
    result = execute_command(':memory-audit project-a', state)
    assert not result.handled and result.state is state


def test_tui_diff_uses_the_actual_project_readmodel_and_renderer(snapshots, monkeypatch):  # noqa: F811
    from sqlmodel import Session

    from agent.repositories.memory_audit import SQLMemoryAuditRepository
    from agent.services.memory_audit_read_service import MemoryAuditQuery, MemoryAuditReadService
    from agent.services.project_access_authority import SqlProjectAccessAuthority
    from agent.services.source_control_access_policy import HubSourcePrincipal

    engine, policy, repository, capture = snapshots
    capture.capture(policy, 'before')
    capture.capture(policy, 'after', scope_outcome='failed')
    service = MemoryAuditReadService(access=SqlProjectAccessAuthority(session_factory=lambda: Session(engine)),
        repository=SQLMemoryAuditRepository(engine), snapshots=repository)
    principal = HubSourcePrincipal('operator', policy.actor.tenant_id, None, frozenset())
    payload = service.diffs(principal=principal, project_id=policy.namespace.workspace_id,
                           query=MemoryAuditQuery(run_id=policy.namespace.run_id))
    client = Mock()
    client.return_value.request_json.return_value = {'data': payload}
    monkeypatch.setattr('client_surfaces.operator_tui.commands_memory_audit.OpsApiClient', client)
    result = execute_command(':memory-audit ' + policy.namespace.workspace_id
        + ' --view diffs --run-id ' + policy.namespace.run_id, OperatorState(endpoint='http://synthetic-hub'))
    assert result.handled
    assert 'Memory-Diffs' in '\n'.join(_content_lines(result.state, 100, height=40))
    assert json.loads(result.state.header_logo_game['audit_viewer']['text']) == payload
    request_path = client.return_value.request_json.call_args.args[1]
    assert '/memory-audit/diffs?' in request_path and 'view=' not in request_path
