"""Hub-issued test identities cross only a signed, Pi-specific explicit consumer boundary."""

import base64
import copy
import time
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlmodel import Session

from agent.db_models import TaskDB
from ananta_contracts.hub_evidence import build_hub_evidence_assignment
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_task_execution import (
    sign_native_execution_request,
    verify_native_execution_envelope,
    verify_native_execution_request,
)
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from tests.pi.test_pi_result_completion_policy import admission as admission  # noqa: F401
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401
from tests.pi.test_pi_run_evidence_reservation import reserve
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.result_store import SQLiteNativeResultStore


@pytest.fixture
def envelope(reservation):
    run = reserve(reservation)
    evidence = reservation.registry.assignment_projection(
        tenant_id=run.tenant_id, project_id=run.project_id, task_id=run.task_id,
        run_id=run.run_id, assignment_id=run.assignment_id, dispatch_lease_id=run.dispatch_lease_id,
    )
    with Session(reservation.admission.engine) as session:
        task = session.get(TaskDB, run.task_id).model_dump()
    signer = Ed25519SigningKeyRing({'synthetic': base64.b64encode(b's' * 32).decode()}, active_key_id='synthetic')
    verifier = Ed25519VerificationKeyRing.from_mapping(signer.verification_mapping())
    recipient = {'worker_id': 'synthetic-worker', 'worker_url': task['assigned_agent_url']}
    value = sign_native_execution_request(task, key_ring=signer, **recipient, expires_at=time.time() + 30,
                                         evidence_assignment=evidence)
    return task, evidence, value, signer, verifier, recipient


def test_signed_registered_projection_does_not_rewrite_immutable_input(envelope):
    task, evidence, value, _signer, verifier, recipient = envelope
    verified = verify_native_execution_envelope(value, key_ring=verifier, **recipient)
    assert verified.evidence_assignment == evidence
    assert native_context_digest(verified.task['worker_execution_context']) == native_context_digest(
        task['worker_execution_context'])
    verified.evidence_assignment['source_ids'].clear()
    assert value['evidence_assignment']['source_ids']
    with pytest.raises(ValueError, match='native_execution_evidence_consumer_required'):
        verify_native_execution_request(value, key_ring=verifier, **recipient)


def test_hub_signing_rejects_foreign_task_or_non_pi_projection(envelope):
    task, evidence, _value, signer, _verifier, recipient = envelope
    foreign = {key: item for key, item in evidence.items() if key not in {'schema', 'projection_digest'}}
    foreign['task_id'] = 'foreign-task'
    with pytest.raises(ValueError, match='native_execution_evidence_task_mismatch'):
        sign_native_execution_request(task, key_ring=signer, **recipient, expires_at=time.time() + 30,
                                      evidence_assignment=build_hub_evidence_assignment(**foreign))
    task = copy.deepcopy(task)
    task['task_kind'] = task['worker_execution_context']['native_node_command']['node']['task_kind'] = 'tool_task'
    with pytest.raises(ValueError, match='native_execution_evidence_task_mismatch'):
        sign_native_execution_request(task, key_ring=signer, **recipient, expires_at=time.time() + 30,
                                      evidence_assignment=evidence)


@pytest.mark.parametrize('change', ['binding', 'remove', 'null'])
def test_worker_cannot_replace_or_drop_the_signed_registry_projection(envelope, change):
    _task, evidence, value, _signer, verifier, recipient = envelope
    if change == 'remove':
        value.pop('evidence_assignment')
    elif change == 'null':
        value['evidence_assignment'] = None
    else:
        modified = {key: item for key, item in evidence.items() if key not in {'schema', 'projection_digest'}}
        modified['binding_digest'] = 'f' * 64
        value['evidence_assignment'] = build_hub_evidence_assignment(**modified)
    with pytest.raises(ValueError):
        verify_native_execution_envelope(value, key_ring=verifier, **recipient)


@pytest.mark.parametrize('available', [False, True])
def test_http_evidence_consumer_is_explicit_and_legacy_consumer_never_runs(envelope, available):
    _task, evidence, value, _signer, verifier, recipient = envelope
    app = Flask(__name__)
    app.config['ROLE'] = 'worker'
    token = 'synthetic-service-key-' + 'x' * 32
    legacy, dedicated, store = Mock(), Mock(return_value={'status': 'completed'}), Mock(return_value=None)
    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: token,
        recipient_provider=lambda: (recipient['worker_id'], recipient['worker_url']),
        verification_provider=lambda: verifier, consumer=legacy,
        evidence_consumer=dedicated if available else None, result_store_provider=store))
    result = app.test_client().post('/api/internal/native-tasks/execute', json=value,
                                   headers={'Authorization': 'Bearer ' + token})
    legacy.assert_not_called()
    if available:
        assert result.status_code == 200
        dedicated.assert_called_once()
        assert dedicated.call_args.args[1] == evidence
        store.assert_called_once()
    else:
        assert result.status_code == 503
        assert result.json['reason_code'] == 'native_execution_evidence_consumer_unavailable'
        dedicated.assert_not_called()
        store.assert_not_called()


@pytest.fixture
def durable_store(tmp_path):
    directory = tmp_path / 'private'
    directory.mkdir(mode=0o700)
    return SQLiteNativeResultStore(directory / 'results.sqlite3')


@pytest.mark.parametrize('change', ['assignment_id', 'dispatch_lease_id', 'binding_digest', 'drop'])
def test_durable_result_refuses_reassignment_and_evidence_downgrade(envelope, durable_store, change):
    task, evidence, _value, _signer, _verifier, _recipient = envelope
    durable_store.reserve(task, evidence_assignment=evidence)
    replacement = None
    if change != 'drop':
        fields = {key: item for key, item in evidence.items() if key not in {'schema', 'projection_digest'}}
        fields[change] = 'f' * 64 if change == 'binding_digest' else 'different-assignment'
        replacement = build_hub_evidence_assignment(**fields)
    with pytest.raises(ValueError, match='publication_denied'):
        durable_store.publish(task, {'status': 'completed'}, evidence_assignment=replacement)
    durable_store.publish(task, {'status': 'completed'}, evidence_assignment=evidence)
    reopened = SQLiteNativeResultStore(durable_store.path)
    assert reopened.read(task, evidence_assignment=evidence) == {'status': 'completed'}
    with pytest.raises(ValueError, match='binding_denied'):
        reopened.read(task, evidence_assignment=replacement)
    with pytest.raises(ValueError, match='already_reserved'):
        reopened.reserve(task, evidence_assignment=replacement)


def test_signed_http_result_roundtrip_preserves_exact_evidence_slot(envelope, durable_store):
    task, evidence, value, signer, verifier, recipient = envelope
    app = Flask(__name__)
    app.config['ROLE'] = 'worker'
    token = 'synthetic-service-key-' + 'x' * 32
    dedicated = Mock(return_value={'status': 'completed'})
    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: token,
        recipient_provider=lambda: (recipient['worker_id'], recipient['worker_url']),
        verification_provider=lambda: verifier, evidence_consumer=dedicated,
        result_store_provider=lambda: durable_store))
    client = app.test_client()
    headers = {'Authorization': 'Bearer ' + token}
    assert client.post('/api/internal/native-tasks/execute', json=value, headers=headers).status_code == 200
    assert client.post('/api/internal/native-tasks/result', json=value, headers=headers).json == {'status': 'completed'}
    # A fresh valid Hub signature cannot rebind an existing result slot.
    fields = {key: item for key, item in evidence.items() if key not in {'schema', 'projection_digest'}}
    fields['dispatch_lease_id'] = 'different-dispatch'
    replacement = sign_native_execution_request(task, key_ring=signer, **recipient,
        expires_at=time.time() + 30, evidence_assignment=build_hub_evidence_assignment(**fields))
    assert client.post('/api/internal/native-tasks/result', json=replacement, headers=headers).status_code == 409
    assert client.post('/api/internal/native-tasks/execute', json=replacement, headers=headers).status_code == 409
    dedicated.assert_called_once()


@pytest.mark.parametrize('decision', ['exact', 'changed', 'unavailable'])
def test_worker_evidence_consumer_requires_current_exact_hub_projection(envelope, decision):
    from worker.runtime.native_graph.pi_evidence import VerifiedPiEvidenceConsumer

    task, evidence, _value, _signer, _verifier, _recipient = envelope
    original_input = native_context_digest(task['worker_execution_context'])
    execution = Mock(return_value={'status': 'completed'})

    def read(*, task, command):
        task['worker_execution_context']['untrusted_mutation'] = True
        assert command.node.task_kind == 'pi_coding_agent'
        command.input_data.clear()
        if decision == 'unavailable':
            raise ValueError('synthetic_hub_unavailable')
        if decision == 'changed':
            fields = {key: item for key, item in evidence.items() if key not in {'schema', 'projection_digest'}}
            fields['dispatch_lease_id'] = 'different-dispatch'
            return build_hub_evidence_assignment(**fields)
        return copy.deepcopy(evidence)

    consumer = VerifiedPiEvidenceConsumer(reader=Mock(read=read), execute=execution)
    if decision == 'exact':
        assert consumer(task, evidence) == {'status': 'completed'}
        assert native_context_digest(execution.call_args.args[0]['worker_execution_context']) == original_input
    else:
        with pytest.raises(ValueError):
            consumer(task, evidence)
        execution.assert_not_called()
    assert native_context_digest(task['worker_execution_context']) == original_input


@pytest.mark.parametrize('source_mutated', [False, True])
def test_signed_http_execution_rechecks_actual_hub_registry_before_pi(envelope, reservation, source_mutated):
    from agent.db_models.evidence_identity import HubSourceEvidenceIdentityDB
    from worker.runtime.native_graph.pi_evidence import VerifiedPiEvidenceConsumer

    task, evidence, value, _signer, verifier, recipient = envelope
    if source_mutated:
        with Session(reservation.admission.engine) as session:
            source = session.get(HubSourceEvidenceIdentityDB,
                (task['tenant_id'], task['project_id'], evidence['source_ids'][0]))
            source.content_digest = 'f' * 64
            session.commit()

    def read(*, task, command):
        return reservation.registry.assignment_projection(tenant_id=task['tenant_id'], project_id=task['project_id'],
            task_id=task['id'], run_id=evidence['run_id'], assignment_id=evidence['assignment_id'],
            dispatch_lease_id=evidence['dispatch_lease_id'],
            input_digest=native_context_digest(task['worker_execution_context']),
            expected_binding_digest=evidence['binding_digest'])

    execution = Mock(return_value={'status': 'completed'})
    app = Flask(__name__)
    app.config['ROLE'] = 'worker'
    token = 'synthetic-service-key-' + 'x' * 32
    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: token,
        recipient_provider=lambda: (recipient['worker_id'], recipient['worker_url']),
        verification_provider=lambda: verifier,
        evidence_consumer=VerifiedPiEvidenceConsumer(reader=Mock(read=read), execute=execution)))
    response = app.test_client().post('/api/internal/native-tasks/execute', json=value,
        headers={'Authorization': 'Bearer ' + token})
    if source_mutated:
        assert response.status_code == 409
        execution.assert_not_called()
    else:
        assert response.status_code == 200
        execution.assert_called_once()


def test_worker_reader_sends_only_bound_current_evidence_request(envelope):
    from worker.runtime.native_graph.contracts import NativeNodeCommand
    from worker.runtime.native_graph.pi_evidence import HubPiEvidenceReader

    task, evidence, *_ = envelope
    client = Mock()
    client.command.return_value = evidence
    command = NativeNodeCommand.from_mapping(task['worker_execution_context']['native_node_command'])
    assert HubPiEvidenceReader(client).read(task=task, command=command) == evidence
    client.command.assert_called_once()
    args, values = client.command.call_args
    assert args == ('pi_evidence_read',)
    assert values['hub_task_id'] == task['id']
    assert values['command_id'] == command.command_id
    assert values['input_digest'] == native_context_digest(task['worker_execution_context'])
    assert values['binding']['authorization_envelope'] == command.authorization.to_dict()
    assert 'run_id' not in values and 'evidence_assignment' not in values


@pytest.mark.parametrize('transport', ['current', 'missing', 'unavailable'])
def test_worker_runtime_composes_existing_execution_only_after_authenticated_read(envelope, monkeypatch, transport):
    from worker.runtime.native_graph import pi_evidence_runtime

    task, evidence, *_ = envelope
    client = Mock()
    client.command.return_value = evidence
    if transport == 'unavailable':
        client.command.side_effect = ValueError('synthetic_hub_unavailable')
    monkeypatch.setattr(pi_evidence_runtime.HttpWorkflowHubDecisionClient, 'from_environment',
        lambda: None if transport == 'missing' else client)
    execution = Mock(return_value={'status': 'completed'})
    monkeypatch.setattr(pi_evidence_runtime, 'consume_delegated_workflow_task', execution)
    if transport == 'current':
        assert pi_evidence_runtime.consume_pi_evidence_task(task, evidence) == {'status': 'completed'}
        execution.assert_called_once()
        assert client.command.call_args.args == ('pi_evidence_read',)
    else:
        with pytest.raises(ValueError):
            pi_evidence_runtime.consume_pi_evidence_task(task, evidence)
        execution.assert_not_called()


def test_prepared_top_level_bundle_reference_survives_signed_dispatch(envelope):
    from ananta_contracts.native_context_bundle import NativeApprovedContext, NativeContextBundleProjection
    from worker.runtime.native_graph.contracts import NativeNodeCommand
    from worker.runtime.native_graph.pi_context import HubPiTaskContextReader

    task, evidence, _value, signer, verifier, recipient = envelope
    task = copy.deepcopy(task)
    task['context_bundle_id'] = 'prepared-test-bundle'
    signed = sign_native_execution_request(task, key_ring=signer, **recipient,
        expires_at=time.time() + 30, evidence_assignment=evidence)
    verified = verify_native_execution_envelope(signed, key_ring=verifier, **recipient)
    assert verified.task['context_bundle_id'] == task['context_bundle_id']
    command = NativeNodeCommand.from_mapping(verified.task['worker_execution_context']['native_node_command'])
    approved = NativeApprovedContext(content='synthetic approved input', policy_digest='a'*64)
    projection = NativeContextBundleProjection.create(hub_task_id=task['id'], bundle_id=task['context_bundle_id'],
        command=command.to_dict(), approved=approved)
    client = Mock()
    client.command.return_value = projection.to_dict()
    assert HubPiTaskContextReader(client).read(task=verified.task, command=command) == projection
    client.command.assert_called_once()
    altered = copy.deepcopy(signed)
    altered['task']['context_bundle_id'] = 'foreign-bundle'
    with pytest.raises(ValueError):
        verify_native_execution_envelope(altered, key_ring=verifier, **recipient)


def test_null_context_reference_keeps_existing_task_projection_unchanged(envelope):
    from ananta_contracts.native_task_execution import native_execution_request

    task, *_ = envelope
    before = copy.deepcopy(task)
    before.pop('context_bundle_id', None)
    after = {**before, 'context_bundle_id': None}
    assert native_execution_request(before) == native_execution_request(after)


@pytest.mark.parametrize('reference', ['', 1, 'bundle\n', 'x' * 257])
def test_invalid_context_reference_cannot_enter_signed_dispatch(envelope, reference):
    task, evidence, _value, signer, _verifier, recipient = envelope
    task = {**task, 'context_bundle_id': reference}
    with pytest.raises(ValueError, match='context_reference_invalid'):
        sign_native_execution_request(task, key_ring=signer, **recipient,
            expires_at=time.time() + 30, evidence_assignment=evidence)


def test_signed_scope_is_detached_task_local_and_reset_after_failure(envelope):
    import contextvars

    from worker.runtime.native_graph.pi_evidence_scope import bind_pi_evidence, require_pi_evidence

    task, evidence, *_ = envelope
    expected = copy.deepcopy(evidence)
    with pytest.raises(RuntimeError, match='synthetic_execution_failure'):
        with bind_pi_evidence(evidence):
            evidence['source_ids'].clear()
            assert require_pi_evidence(task_id=task['id']) == expected
            detached = require_pi_evidence(task_id=task['id'])
            detached['source_ids'].clear()
            assert require_pi_evidence(task_id=task['id']) == expected
            with pytest.raises(ValueError, match='scope_required'):
                contextvars.Context().run(require_pi_evidence, task_id=task['id'])
            with pytest.raises(ValueError, match='task_mismatch'):
                require_pi_evidence(task_id='foreign-task')
            raise RuntimeError('synthetic_execution_failure')
    with pytest.raises(ValueError, match='scope_required'):
        require_pi_evidence(task_id=task['id'])


def test_node_rejects_changed_assignment_after_signed_consumer_check(envelope, tmp_path):
    from tests.pi.test_pi_native_node import native_setup
    from worker.runtime.native_graph.pi_evidence_scope import bind_pi_evidence

    _task, evidence, *_ = envelope
    fields = {key: item for key, item in evidence.items() if key not in {'schema', 'projection_digest'}}
    fields['assignment_id'] = 'different-assignment'
    changed = build_hub_evidence_assignment(**fields)
    reader = Mock(read=Mock(return_value=changed))
    adapter, task, runner, client, credentials = native_setup(tmp_path, evidence_reader_factory=lambda _: reader)
    with bind_pi_evidence(evidence):
        result = adapter.execute_task(task)
    assert result.status == 'failed' and result.reason_code == 'pi_execution_evidence_assignment_mismatch'
    assert runner.calls == []
    credentials.assert_not_called()
    assert 'provider_budget_reserve' not in [name for name, _ in client.calls]
