"""Real SQL/source/Worker HTTP/SDK/result Registry chain with synthetic loopback model."""

import json
import os
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
from flask import Flask
from sqlmodel import Session, select

from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess
from agent.cli_backends.pi_runtime import pi_sdk_command
from agent.db_models import ArtifactVersionDB, TaskDB
from agent.db_models.blueprints import ContextBundleDB
from agent.db_models.context_policy_lifecycle import ContextPolicyVersionDB
from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB
from agent.db_models.native_dispatch import NativeDispatchDB
from agent.services.artifact_store import ArtifactStore
from agent.services.native_artifact_runtime import NativeArtifactRuntime
from agent.services.native_dispatch_journal import SQLAlchemyNativeDispatchJournal
from agent.services.pi_approved_context_reader import TransactionPiApprovedContextReader
from agent.services.pi_evidence_policy import PiEvidencePolicyPin, PinnedPiEvidencePolicy
from ananta_contracts.runtime_authorization_crypto import Ed25519VerificationKeyRing
from tests.native.test_native_context_policy import build_policy_setup
from tests.pi import test_pi_result_completion_policy as admission_module
from tests.pi.test_pi_answer_artifact_admission import SQLTransport, artifact_command
from tests.pi.test_pi_dispatch_transaction import assigned, preparation
from tests.pi.test_pi_native_node import native_setup
from tests.pi.test_pi_run_evidence_reservation import reservation as reservation  # noqa: F401
from worker.runtime.native_graph.artifact_publication import NativeArtifactPublisher
from worker.runtime.native_graph.contracts import NativeNodeCommand
from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
from worker.runtime.native_graph.pi_artifact import PiAnswerArtifactPublisher
from worker.runtime.native_graph.pi_context import HubPiTaskContextReader
from worker.runtime.native_graph.pi_evidence import HubPiEvidenceReader, VerifiedPiEvidenceConsumer
from worker.runtime.native_graph.pi_process import pi_execution_launch
from worker.runtime.workflow_adapter_task_consumer import WorkflowAdapterTaskConsumer
from worker.runtime.workflow_adapter_task_execution import consume_delegated_workflow_task
from worker.runtime.workflow_hub_gateway import HubExecutionAuthorizationAdapter


@pytest.fixture(params=[False, True], ids=['plain_answer', 'answer_artifact'])
def sdk_chain(tmp_path, monkeypatch, request):
    node, sdk = os.environ.get('ANANTA_TEST_NODE_BINARY'), os.environ.get('ANANTA_TEST_PI_SDK')
    if not node or not sdk:
        pytest.skip('Explicit Node and pinned SDK resources required')
    node, sdk = Path(node).resolve(strict=True), Path(sdk).resolve(strict=True)
    binary = sdk.parent / 'bundle' / 'cli.js'
    argv = pi_sdk_command(str(binary), path_environment=str(node.parent))
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    canary = private / 'results.sqlite3'
    canary.write_text('synthetic-private-result')
    monkeypatch.setenv('ANANTA_NATIVE_RESULT_STORE_PATH', str(canary))
    monkeypatch.setattr('agent.cli_backends.pi_configuration.pi_context_window', lambda target: 8192)
    state = SimpleNamespace(engine=None, requests=[], artifacts=request.param, root=tmp_path)
    transport = SQLTransport()
    publisher = PiAnswerArtifactPublisher(publisher_factory=lambda: NativeArtifactPublisher(transport=transport))
    state.artifact_transport = transport

    class Model(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.connection.settimeout(3)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < 200000:
                self.send_error(400)
                return
            body = json.loads(self.rfile.read(length))
            with Session(state.engine) as session:
                run = session.exec(select(HubRunEvidenceIdentityDB)).one()
                state.requests.append((self.path, body, run.state, len(run.source_ids)))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for delta, reason in [({'role': 'assistant', 'content': 'SQL-bound SDK answer'}, None), ({}, 'stop')]:
                chunk = {'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode())
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Model)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runtime = {'installed': True, 'status': 'ready', 'version': '0.85.1', 'binary_path': str(binary),
            'version_probe': {'rc': 0, 'stdout': '0.85.1', 'stderr': ''}}
        adapter, task, _, client, _ = native_setup(tmp_path,
            mutate=artifact_command if state.artifacts else lambda command: command,
            artifact_publisher=publisher if state.artifacts else None,
            endpoint_identity=f'http://127.0.0.1:{server.server_port}/v1/chat/completions',
            runner=BoundedCodingAgentProcess(launch_context=pi_execution_launch),
            context_reader_factory=HubPiTaskContextReader,
            evidence_reader_factory=HubPiEvidenceReader,
            provider_options={'runtime_probe': lambda: runtime, 'command_builder': lambda _: argv,
                              'environment': {'PATH': str(node.parent)}})
        state.adapter, state.client, state.canary = adapter, client, canary
        state.command = NativeNodeCommand.from_mapping(task['worker_execution_context']['native_node_command'])
        state.task_id = task['id']
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.fixture
def admission(tmp_path, monkeypatch, sdk_chain):
    monkeypatch.setattr(admission_module, 'actual_route', lambda path: (None, sdk_chain.command, sdk_chain.task_id))
    yield from admission_module.admission.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.timeout(45)
@pytest.mark.parametrize('mutated_context', [False, True])
def test_sdk_output_closes_only_the_pre_reserved_sql_context_run(reservation, sdk_chain, monkeypatch, mutated_context):
    value = reservation
    sdk_chain.engine = value.admission.engine
    if sdk_chain.artifacts:
        sdk_chain.artifact_transport.runtime = NativeArtifactRuntime(engine=value.admission.engine,
            key_ring=lambda: value.admission.keys, storage=ArtifactStore(sdk_chain.root / 'hub-artifacts'),
            clock=lambda: value.admission.clock.now)
    setup = build_policy_setup(monkeypatch)
    bundle_data, destinations = setup[2], setup[5]
    snapshot = setup[4].active.return_value
    bundle_data['bundle_metadata']['native_context_access']['provider_endpoint_identity'] = (
        sdk_chain.command.provider_binding.endpoint_identity)
    with Session(value.admission.engine) as session, session.begin():
        task = session.get(TaskDB, value.admission.task_id)
        task.context_bundle_id = 'sdk-chain-bundle'
        session.add(ContextBundleDB(id=task.context_bundle_id, task_id=task.id, **bundle_data))
        session.add(ContextPolicyVersionDB(record_id='sdk-chain-policy', tenant_id=task.tenant_id,
            project_id=task.project_id, policy_id=snapshot.policy_id, version=snapshot.version, state='active',
            document_json=snapshot.document, policy_digest=snapshot.policy_digest, etag=snapshot.etag,
            created_by=snapshot.created_by, created_at=snapshot.created_at,
            updated_by=snapshot.created_by, updated_at=snapshot.created_at))
    manifest = replace(value.values['manifest'], node_version='24.21.0')
    value.service._manifests._deployments.require_manifest = lambda **identity: manifest
    context = value.admission.command.provider_context
    pin = PiEvidencePolicyPin(manifest=manifest, policy_version=value.admission.command.policy_version,
        provider_profile_id=context['provider_profile_id'], provider_id=context['selected_provider_id'],
        model_id=context['selected_model_id'], preauthorized_read_only=True,
        preauthorized_result_artifacts=sdk_chain.artifacts)
    policy = PinnedPiEvidencePolicy(pins=[pin], deployments=value.service._manifests._deployments)
    service = preparation(value, contexts=TransactionPiApprovedContextReader(destinations=destinations),
        policy_digest=policy.digest_for_task)
    prepared = service.prepare(task_id=value.admission.task_id, assignment=assigned(value))
    if mutated_context:
        with Session(value.admission.engine) as session, session.begin():
            bundle = session.get(ContextBundleDB, 'sdk-chain-bundle')
            chunks = [dict(chunk) for chunk in bundle.chunks]
            chunks[0]['content'] = 'changed after reservation'
            bundle.chunks = chunks
    sdk_chain.client.service._pi_evidence = service.reserved_assignment
    sdk_chain.client.service._pi_contexts = lambda **binding: service.reserved_context(**binding).to_dict()
    app = Flask(__name__)
    app.config['ROLE'] = 'worker'
    app.extensions['workflow_adapter_task_consumer'] = WorkflowAdapterTaskConsumer(
        authorization=HubExecutionAuthorizationAdapter(sdk_chain.client), native_adapter=sdk_chain.adapter)
    token = 'synthetic-worker-token-' + 'x'*32
    app.register_blueprint(create_native_execution_blueprint(token_provider=lambda: token,
        recipient_provider=lambda: ('worker-1', 'http://worker-1:5000'),
        verification_provider=lambda: Ed25519VerificationKeyRing.from_mapping(
            service._signing_keys().verification_mapping()),
        evidence_consumer=VerifiedPiEvidenceConsumer(reader=HubPiEvidenceReader(sdk_chain.client),
                                                    execute=consume_delegated_workflow_task)))
    response = app.test_client().post('/api/internal/native-tasks/execute', json=prepared.payload,
        headers={'Authorization': 'Bearer ' + token})
    if mutated_context:
        assert response.status_code == 409
        assert sdk_chain.requests == []
        with Session(value.admission.engine) as session:
            run = session.exec(select(HubRunEvidenceIdentityDB)).one()
            assert run.state == 'reserved' and len(run.source_ids) == 3
        return
    if response.status_code != 200 or response.json.get('status') != 'completed':
        raise ValueError('pi_sql_sdk_chain_worker_failed')
    value.admission.response = response.json
    accepted = admission_module.complete(value.admission)
    run = service.complete_reserved(task_id=accepted.id)
    SQLAlchemyNativeDispatchJournal(value.admission.engine).settle(task=accepted, claim=prepared.claim, accepted=True)
    assert run.state == 'succeeded' and run.synthetic is True
    assert run.run_id == prepared.payload['evidence_assignment']['run_id']
    assert len(sdk_chain.requests) == 1
    path, body, state, sources = sdk_chain.requests[0]
    assert path == '/v1/chat/completions' and body['model'] == 'selected-model'
    assert state == 'reserved' and sources == 3
    rendered = json.dumps(body['messages'])
    assert 'def example(): return 1' in rendered and 'unapproved assembled' not in rendered
    assert not body.get('tools')
    assert sdk_chain.canary.read_text() == 'synthetic-private-result'
    with Session(value.admission.engine) as session:
        assert session.exec(select(NativeDispatchDB)).one().state == 'accepted'
        assert session.exec(select(HubRunEvidenceIdentityDB)).one().state == 'succeeded'
        if sdk_chain.artifacts:
            version = session.exec(select(ArtifactVersionDB)).one()
            assert Path(version.storage_path).read_bytes() == b'SQL-bound SDK answer'
