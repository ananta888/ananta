"""Actual SQL/Hub immutable storage/Worker artifact publication and Pi RUN closure."""

import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.common.pi_task_result_binding import PI_RESULT_RECEIPT
from agent.db_models import ArtifactDB, ArtifactVersionDB
from agent.services.artifact_store import ArtifactStore
from agent.services.native_artifact_runtime import NativeArtifactRuntime
from agent.services.pi_evidence_policy import PiEvidencePolicyPin, PinnedPiEvidencePolicy
from agent.services.pi_run_evidence_completion import PiRunEvidenceCompletionService
from ananta_contracts.runtime_authorization_crypto import Ed25519VerificationKeyRing
from tests.pi import test_pi_result_completion_policy as admission_module
from tests.pi.test_pi_dispatch_transaction import assigned, preparation
from tests.pi.test_pi_native_node import native_setup
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401
from tests.task_scoped_forwarding_seam import forwarding_dependencies  # noqa: F401
from worker.runtime.native_graph.artifact_publication import NativeArtifactPublisher
from worker.runtime.native_graph.contracts import NativeNodeCommand
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.pi_artifact import PiAnswerArtifactPublisher
from worker.runtime.native_graph.pi_evidence import HubPiEvidenceReader, VerifiedPiEvidenceConsumer
from worker.runtime.workflow_adapter_task_consumer import WorkflowAdapterTaskConsumer
from worker.runtime.workflow_adapter_task_execution import consume_delegated_workflow_task
from worker.runtime.workflow_hub_gateway import HubExecutionAuthorizationAdapter


def artifact_command(command):
    return replace(command, node=replace(command.node, output_artifacts=('not-materialized',),
        metadata={**command.node.metadata, 'pi_result_artifact': {
            'output_name': 'not-materialized', 'source': 'assistant_answer',
            'filename': 'answer.md', 'media_type': 'text/markdown'}}))


class SQLTransport:
    def __init__(self):
        self.runtime = None
        self.transfers = []

    def request(self, *, method, path, payload=None):
        assert path == '/internal/tasks/hub-task-1/native-artifacts'
        if method == 'POST':
            self.transfers.append(copy.deepcopy(payload))
        return self.runtime.execute(worker_id='worker-1', worker_url='http://worker-1:5000',
            task_id='hub-task-1', transfer=payload)


@pytest.fixture
def artifact_worker(tmp_path):
    transport = SQLTransport()
    publisher = PiAnswerArtifactPublisher(publisher_factory=lambda: NativeArtifactPublisher(transport=transport))
    adapter, task, runner, client, credentials = native_setup(tmp_path,
        mutate=artifact_command, artifact_publisher=publisher, evidence_reader_factory=HubPiEvidenceReader)
    command = NativeNodeCommand.from_mapping(task['worker_execution_context']['native_node_command'])
    return SimpleNamespace(transport=transport, adapter=adapter, command=command,
        client=client, runner=runner, credentials=credentials)


@pytest.fixture
def admission(tmp_path, monkeypatch, artifact_worker):
    monkeypatch.setattr(admission_module, 'actual_route', lambda path: (None, artifact_worker.command, 'hub-task-1'))
    yield from admission_module.admission.__wrapped__(tmp_path, monkeypatch)


@pytest.fixture
def published(reservation, artifact_worker, tmp_path):
    value = reservation
    store = ArtifactStore(tmp_path / 'hub-artifacts')
    artifact_worker.transport.runtime = NativeArtifactRuntime(engine=value.admission.engine,
        key_ring=lambda: value.admission.keys, storage=store, clock=lambda: value.admission.clock.now)
    service = preparation(value)
    prepared = service.prepare(task_id=value.admission.task_id, assignment=assigned(value))
    artifact_worker.client.service._pi_evidence = service.reserved_assignment
    app = Flask(__name__)
    app.config['ROLE'] = 'worker'
    app.extensions['workflow_adapter_task_consumer'] = WorkflowAdapterTaskConsumer(
        authorization=HubExecutionAuthorizationAdapter(artifact_worker.client), native_adapter=artifact_worker.adapter)
    token = 'synthetic-worker-token-' + 'x' * 32
    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: token,
        recipient_provider=lambda: ('worker-1', 'http://worker-1:5000'),
        verification_provider=lambda: Ed25519VerificationKeyRing.from_mapping(
            service._signing_keys().verification_mapping()),
        evidence_consumer=VerifiedPiEvidenceConsumer(reader=HubPiEvidenceReader(artifact_worker.client),
            execute=consume_delegated_workflow_task)))
    response = app.test_client().post('/api/internal/native-tasks/execute', json=prepared.payload,
        headers={'Authorization': 'Bearer ' + token})
    assert response.status_code == 200 and response.json['status'] == 'completed'
    value.admission.response = response.json
    return SimpleNamespace(value=value, worker=artifact_worker, store=store, service=service, prepared=prepared)


def test_answer_artifact_uses_existing_hub_ingress_and_same_reserved_run(published):
    value = published.value
    accepted = admission_module.complete(value.admission)
    receipt = accepted.verification_status[PI_RESULT_RECEIPT]
    assert len(receipt['artifacts']) == 1
    run = published.service.complete_reserved(task_id=accepted.id)
    assert run.state == 'succeeded' and run.run_id == published.prepared.payload['evidence_assignment']['run_id']
    with Session(value.admission.engine) as session:
        artifact = session.exec(select(ArtifactDB)).one()
        version = session.exec(select(ArtifactVersionDB)).one()
        assert artifact.created_by == 'hub_native_artifact_ingress'
        assert Path(version.storage_path).read_bytes() == 'A\u2028B'.encode()
        assert receipt['artifacts'][0]['artifact_id'] == artifact.id
    # Transport retry is idempotent while the actual Task/dispatch is active,
    # so test it before completion in the separate case below.
    value.admission.clock.now += 1000
    assert admission_module.complete(value.admission).verification_status[PI_RESULT_RECEIPT] == receipt
    assert published.service.complete_reserved(task_id=accepted.id) == run


def test_artifact_transport_retry_does_not_create_another_registration(published):
    transfer = published.worker.transport.transfers[0]
    runtime = published.worker.transport.runtime
    first = runtime.execute(worker_id='worker-1', worker_url='http://worker-1:5000',
        task_id=published.value.admission.task_id, transfer=transfer)
    second = runtime.execute(worker_id='worker-1', worker_url='http://worker-1:5000',
        task_id=published.value.admission.task_id, transfer=transfer)
    assert first == second
    with Session(published.value.admission.engine) as session:
        assert len(session.exec(select(ArtifactDB)).all()) == 1
        assert len(session.exec(select(ArtifactVersionDB)).all()) == 1


@pytest.mark.parametrize('change', ['answer', 'metadata', 'missing', 'expired'])
def test_artifact_or_authority_mutation_prevents_pi_success_admission(published, change):
    admission = published.value.admission
    if change == 'answer':
        nested = admission.response['workflow_adapter_verification']['workflow_adapter_task_result']['adapter_result']
        nested['output_data']['output'] = 'different answer'
        nested['verification']['native_node_result']['output_data']['output'] = 'different answer'
    elif change == 'expired':
        admission.clock.now += 1000
    else:
        with Session(admission.engine) as session, session.begin():
            artifact = session.exec(select(ArtifactDB)).one()
            if change == 'missing':
                session.delete(artifact)
            else:
                artifact.artifact_metadata = {'forged': True}
    with pytest.raises(ValueError):
        admission_module.complete(admission)
    assert admission.repository.get_by_id(admission.task_id).status == 'in_progress'


def test_corrupt_admitted_artifact_blocks_receipt_replay_and_registry_closure(published):
    value = published.value
    accepted = admission_module.complete(value.admission)
    with Session(value.admission.engine) as session, session.begin():
        artifact = session.exec(select(ArtifactDB)).one()
        artifact.latest_sha256 = '0' * 64
    with pytest.raises(ValueError):
        admission_module.complete(value.admission)
    with Session(value.admission.engine) as session, pytest.raises(ValueError):
        PiRunEvidenceCompletionService(registry=published.value.registry).complete(session=session,
            task_id=accepted.id, assignment=published.prepared.payload['evidence_assignment'])


def test_actual_forwarding_preserves_only_verified_pi_artifact_references(
    published, monkeypatch, forwarding_dependencies,  # noqa: F811 - imported pytest fixture
):
    from agent.services import _task_scoped_forwarding as forwarding
    from agent.services import task_runtime_service

    admission = published.value.admission
    normalizer = Mock(side_effect=AssertionError('Pi receipt must not enter generic artifact registration'))
    forwarding_dependencies(repositories=lambda: SimpleNamespace(task_repo=admission.repository),
        normalize_artifacts=normalizer)
    monkeypatch.setattr(task_runtime_service, 'task_repo', admission.repository)
    task = admission.repository.get_by_id(admission.task_id).model_dump()
    forwarding.persist_forwarded_execution(tid=admission.task_id, response=admission.response,
        task=task, request_data=SimpleNamespace(command=None))
    accepted = admission.repository.get_by_id(admission.task_id)
    assert accepted.status == 'completed'
    assert accepted.verification_status['execution_artifacts'] == admission.response['artifacts']
    assert len(accepted.verification_status[PI_RESULT_RECEIPT]['artifacts']) == 1
    assert published.service.complete_reserved(task_id=accepted.id).state == 'succeeded'
    normalizer.assert_not_called()


def test_artifact_publication_past_node_deadline_cannot_return_success(tmp_path):
    state = SimpleNamespace(now=0)

    class Publication:
        def prepare(self, **binding):
            return self

        def publish(self, answer):
            state.now += 301
            return {'not-materialized': 'artifact://native-artifact-' + 'a' * 64}

    adapter, task, runner, client, credentials = native_setup(tmp_path,
        mutate=artifact_command, artifact_publisher=Publication())
    import time

    state.now = time.time()
    adapter._runtime._handler._clock = lambda: state.now
    result = adapter.execute_task(task)
    assert result.status == 'failed' and result.reason_code == 'pi_deadline_expired'
    assert len(runner.calls) == 1 and not result.artifact_refs


def test_artifact_policy_requires_additional_explicit_preauthorization(reservation):
    value = reservation
    context = value.admission.command.provider_context
    pin = PiEvidencePolicyPin(manifest=value.values['manifest'], policy_version=value.admission.command.policy_version,
        provider_profile_id=context['provider_profile_id'], provider_id=context['selected_provider_id'],
        model_id=context['selected_model_id'], preauthorized_read_only=True)
    task = value.admission.repository.get_by_id(value.admission.task_id)
    deployments = value.service._manifests._deployments
    with pytest.raises(ValueError, match='task_denied'):
        PinnedPiEvidencePolicy(pins=[pin], deployments=deployments).digest_for_task(task)
    enabled = replace(pin, preauthorized_result_artifacts=True)
    assert enabled.digest != pin.digest
    assert PinnedPiEvidencePolicy(pins=[enabled], deployments=deployments).digest_for_task(task) == enabled.digest


def test_missing_artifact_transport_denies_before_credentials_budget_or_sdk(tmp_path):
    adapter, task, runner, client, credentials = native_setup(tmp_path, mutate=artifact_command)
    result = adapter.execute_task(task)
    assert result.reason_code == 'pi_result_artifact_transport_unavailable'
    assert runner.calls == []
    credentials.assert_not_called()
    assert not any(name == 'provider_budget_reserve' for name, _ in client.calls)


@pytest.mark.parametrize('change', ['path', 'media', 'unknown', 'undeclared', 'unsigned_output'])
def test_invalid_artifact_declaration_never_reaches_credentials_or_sdk(tmp_path, change):
    def mutate(command):
        command = artifact_command(command)
        metadata = copy.deepcopy(command.node.metadata)
        descriptor = metadata['pi_result_artifact']
        if change == 'path':
            descriptor['filename'] = '../private/result.db'
        elif change == 'media':
            descriptor['media_type'] = []
        elif change == 'unknown':
            descriptor['authority'] = True
        elif change == 'undeclared':
            descriptor['output_name'] = 'foreign'
        else:
            descriptor['output_name'] = 'not-authorized'
            return replace(command, node=replace(command.node, output_artifacts=('not-authorized',), metadata=metadata))
        return replace(command, node=replace(command.node, metadata=metadata))

    adapter, task, runner, client, credentials = native_setup(tmp_path, mutate=mutate)
    result = adapter.execute_task(task)
    assert result.status == 'failed'
    assert runner.calls == []
    credentials.assert_not_called()
