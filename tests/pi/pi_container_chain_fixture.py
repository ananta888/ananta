"""Synthetic acceptance fixture: Hub owns SQL/identity/signing, Worker owns SDK.

Executed only in containers owned by pi_container_chain_gate.py. Hub gateway
budget and deployment ports use existing deterministic test fixtures. This is
not production evidence and never activates a serving deployment.
"""

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from threading import Thread

import pytest
from flask import Flask, jsonify, request
from sqlmodel import Session, select
from werkzeug.serving import make_server

TOKEN = 'synthetic-pi-container-chain-token-1234567890'
HUB = 'http://hub:5100'


def http_json(url, *, value=None, timeout=30):
    body = None if value is None else json.dumps(value).encode()
    req = urllib.request.Request(url, data=body, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def wait_json(url):
    deadline = time.monotonic() + 40
    while True:
        try:
            return http_json(url, timeout=2)
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise ValueError('pi_chain_peer_readiness_timeout') from None
            time.sleep(.25)


def app():
    from agent.ai_agent import create_app
    from agent.database import init_db

    init_db()
    result = create_app(testing=True)
    result.config.update(AGENT_TOKEN=TOKEN, AGENT_TOKEN_FILE=None)
    return result


def hub():
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
    from agent.services.workflow_worker_gateway_service import WorkflowWorkerGatewayError
    from tests.native.test_native_context_policy import build_policy_setup
    from tests.pi import test_pi_result_completion_policy as admission_module
    from tests.pi.test_pi_answer_artifact_admission import artifact_command
    from tests.pi.test_pi_dispatch_transaction import assigned, preparation
    from tests.pi.test_pi_hub_budget_composition import composition
    from tests.pi.test_pi_native_node import task_command
    from tests.pi.test_pi_run_evidence_reservation import reservation

    application = app()
    root = Path('/tmp/pi-chain-hub')
    root.mkdir(mode=0o700)
    client, context, _ = composition(endpoint_identity='http://ollama:5101/v1/chat/completions',
        allowed_artifacts=('not-materialized', 'unhydrated'))
    command = task_command(context)
    artifact_output = os.environ.get('ANANTA_TEST_PI_CHAIN_ANSWER_ARTIFACT') == '1'
    if artifact_output:
        command = artifact_command(command)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(admission_module, 'actual_route', lambda path: (None, command, 'hub-task-1'))
        lifecycle = admission_module.admission.__wrapped__(root, patch)
        admission = next(lifecycle)
        try:
            value = reservation.__wrapped__(admission)
            setup = build_policy_setup(patch)
            bundle, destinations = setup[2], setup[5]
            policy = setup[4].active.return_value
            bundle['bundle_metadata']['native_context_access']['provider_endpoint_identity'] = (
                command.provider_binding.endpoint_identity)
            with Session(admission.engine) as session, session.begin():
                task = session.get(TaskDB, admission.task_id)
                task.context_bundle_id = 'container-chain-context'
                session.add(ContextBundleDB(id=task.context_bundle_id, task_id=task.id, **bundle))
                session.add(ContextPolicyVersionDB(record_id='container-chain-policy', tenant_id=task.tenant_id,
                    project_id=task.project_id, policy_id=policy.policy_id, version=policy.version, state='active',
                    document_json=policy.document, policy_digest=policy.policy_digest, etag=policy.etag,
                    created_by=policy.created_by, created_at=policy.created_at,
                    updated_by=policy.created_by, updated_at=policy.created_at))
            pin = PiEvidencePolicyPin(manifest=value.values['manifest'], policy_version=command.policy_version,
                provider_profile_id=context.provider_profile_id, provider_id=context.selected_provider_id,
                model_id=context.selected_model_id, preauthorized_read_only=True,
                preauthorized_result_artifacts=artifact_output)
            policy_port = PinnedPiEvidencePolicy(pins=[pin], deployments=value.service._manifests._deployments)
            service = preparation(value, contexts=TransactionPiApprovedContextReader(destinations=destinations),
                policy_digest=policy_port.digest_for_task)
            prepared = service.prepare(task_id=admission.task_id, assignment=assigned(value))
            mutated_context = os.environ.get('ANANTA_TEST_PI_CHAIN_CONTEXT_MUTATION') == '1'
            if mutated_context:
                with Session(admission.engine) as session, session.begin():
                    stored = session.get(ContextBundleDB, 'container-chain-context')
                    chunks = [dict(chunk) for chunk in stored.chunks]
                    chunks[0]['content'] = 'changed after Hub reservation'
                    stored.chunks = chunks
            client.service._pi_evidence = service.reserved_assignment
            client.service._pi_contexts = lambda **binding: service.reserved_context(**binding).to_dict()
            observations = []

            @application.before_request
            def fixture_auth():
                if request.path.startswith('/fixture/'):
                    if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
                        return jsonify(reason_code='fixture_auth_required'), 401

            @application.get('/fixture/bootstrap')
            def bootstrap():
                return jsonify(context=command.provider_context,
                    verification=service._signing_keys().verification_mapping())

            def worker_command():
                if request.headers.get('Authorization') != 'Bearer ' + TOKEN:
                    return jsonify(reason_code='fixture_auth_required'), 401
                try:
                    return jsonify(client.service.execute(request.get_json(),
                        authenticated_worker_id='worker-1', authenticated_worker_url='http://worker-1:5000'))
                except WorkflowWorkerGatewayError as exc:
                    return jsonify(reason_code=exc.reason_code), 409

            # Only this owned fixture app replaces the transport auth shell.
            # The actual gateway service still validates grants, leases and budgets.
            rule = next(rule for rule in application.url_map.iter_rules()
                        if rule.rule == '/api/internal/workflow-runtime/worker-commands')
            application.view_functions[rule.endpoint] = worker_command

            if artifact_output:
                artifact_runtime = NativeArtifactRuntime(engine=admission.engine, key_ring=lambda: admission.keys,
                    storage=ArtifactStore(root / 'artifacts'), clock=lambda: admission.clock.now)

                def artifact_ingress(task_id):
                    if (request.headers.get('Authorization') != 'Bearer ' + TOKEN
                            or request.headers.get('X-Ananta-Worker-ID') != 'worker-1'
                            or request.headers.get('X-Ananta-Worker-URL') != 'http://worker-1:5000'):
                        return jsonify(reason_code='fixture_artifact_auth_required'), 401
                    transfer = request.get_json() if request.method == 'POST' else None
                    return jsonify(data=artifact_runtime.execute(worker_id='worker-1',
                        worker_url='http://worker-1:5000', task_id=task_id, transfer=transfer))

                rule = next(rule for rule in application.url_map.iter_rules()
                            if rule.rule == '/api/internal/tasks/<task_id>/native-artifacts')
                application.view_functions[rule.endpoint] = artifact_ingress

            model_app = Flask('synthetic_model', static_folder=None)

            @model_app.post('/v1/chat/completions')
            def model():
                # SDK credentials are synthetic, distinct from Hub RPC credentials.
                if request.headers.get('Authorization') != 'Bearer synthetic-private-key':
                    return jsonify(reason_code='fixture_model_auth_required'), 401
                body = request.get_json()
                with Session(admission.engine) as session:
                    run = session.exec(select(HubRunEvidenceIdentityDB)).one()
                    observations.append((body, run.state, len(run.source_ids)))
                deltas = [({'role': 'assistant', 'content': 'Container SDK answer'}, None), ({}, 'stop')]
                chunks = [{'choices': [{'index': 0, 'delta': delta, 'finish_reason': reason}]}
                          for delta, reason in deltas]
                return model_app.response_class(''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks)
                    + 'data: [DONE]\n\n', mimetype='text/event-stream')

            server = make_server('0.0.0.0', 5100, application, threaded=True)
            thread = Thread(target=server.serve_forever, daemon=True)
            model_server = make_server('0.0.0.0', 5101, model_app, threaded=True)
            model_thread = Thread(target=model_server.serve_forever, daemon=True)
            thread.start()
            model_thread.start()
            try:
                wait_json('http://worker-1:5000/fixture/ready')
                if mutated_context:
                    try:
                        http_json('http://worker-1:5000/api/internal/native-tasks/execute', value=prepared.payload)
                    except urllib.error.HTTPError as exc:
                        assert exc.code == 409
                        assert json.load(exc)['reason_code'] == 'native_execution_consumer_failed'
                        exc.close()
                    else:
                        raise ValueError('pi_container_changed_context_allowed')
                    assert observations == []
                    with Session(admission.engine) as session:
                        runs = session.exec(select(HubRunEvidenceIdentityDB)).all()
                        assert len(runs) == 1
                        run = runs[0]
                        assert run.state == 'reserved' and len(run.source_ids) == 3
                        assert run.run_id == prepared.payload['evidence_assignment']['run_id']
                    print('PI_CONTAINER_CHAIN_RESULT=' + json.dumps({
                        'classification': 'synthetic_two_container_changed_context_denied',
                        'production_release_evidence': False, 'model_requests': 0,
                        'reserved_sources': 3, 'run_state': run.state,
                        'http_status': 409, 'same_reserved_run': True}), flush=True)
                    return
                response = http_json('http://worker-1:5000/api/internal/native-tasks/execute', value=prepared.payload)
                if response.get('status') != 'completed':
                    codes = []
                    def reasons(value):
                        if isinstance(value, dict):
                            for key, item in value.items():
                                if key == 'reason_code' and isinstance(item, str) and item.replace('_', '').isalnum():
                                    codes.append(item[:128])
                                reasons(item)
                        elif isinstance(value, list):
                            for item in value:
                                reasons(item)
                    reasons(response)
                    print('PI_CONTAINER_FAILURE_CODES=' + json.dumps(codes), flush=True)
                    raise ValueError('pi_container_worker_not_completed')
                retrieved = http_json('http://worker-1:5000/api/internal/native-tasks/result', value=prepared.payload)
                assert retrieved == response
                try:
                    http_json('http://worker-1:5000/api/internal/native-tasks/execute', value=prepared.payload)
                except urllib.error.HTTPError as exc:
                    assert exc.code == 409
                    assert json.load(exc)['reason_code'] == 'native_execution_consumer_failed'
                    exc.close()
                else:
                    raise ValueError('pi_container_duplicate_execution_allowed')
                admission.response = response
                accepted = admission_module.complete(admission)
                run = service.complete_reserved(task_id=accepted.id)
                SQLAlchemyNativeDispatchJournal(admission.engine).settle(
                    task=accepted, claim=prepared.claim, accepted=True)
                assert run.state == 'succeeded' and run.synthetic
                assert run.run_id == prepared.payload['evidence_assignment']['run_id']
                assert len(observations) == 1
                body, state, count = observations[0]
                assert state == 'reserved' and count == 3 and body['model'] == 'selected-model'
                assert not body.get('tools')
                rendered = json.dumps(body['messages'])
                assert 'def example(): return 1' in rendered and 'unapproved assembled' not in rendered
                with Session(admission.engine) as session:
                    assert session.exec(select(NativeDispatchDB)).one().state == 'accepted'
                    if artifact_output:
                        version = session.exec(select(ArtifactVersionDB)).one()
                        assert Path(version.storage_path).read_bytes() == b'Container SDK answer'
                print('PI_CONTAINER_CHAIN_RESULT=' + json.dumps({
                    'classification': 'synthetic_two_container_sql_sdk_chain',
                    'production_release_evidence': False, 'model_requests': 1,
                    'reserved_sources_before_model': count, 'run_state': run.state,
                    'journal_state': 'accepted', 'same_reserved_run': True,
                    'stored_result_matches': True, 'duplicate_execute_status': 409,
                    'hub_admitted_answer_artifacts': int(artifact_output)}), flush=True)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
                model_server.shutdown()
                model_server.server_close()
                model_thread.join(timeout=3)
        finally:
            lifecycle.close()


def worker():
    from agent.cli_backends.coding_agent_process import BoundedCodingAgentProcess
    from agent.cli_backends.pi_runtime import pi_sdk_command
    from ananta_contracts.provider_invocation import ProviderInvocationContext
    from tests.pi.test_pi_answer_artifact_admission import artifact_command
    from tests.pi.test_pi_native_node import native_setup
    from worker.runtime.native_graph.pi_artifact import PiAnswerArtifactPublisher, configured_pi_answer_publisher
    from worker.runtime.native_graph.pi_context import HubPiTaskContextReader
    from worker.runtime.native_graph.pi_evidence import HubPiEvidenceReader
    from worker.runtime.native_graph.pi_process import pi_execution_launch
    from worker.runtime.workflow_adapter_task_consumer import WorkflowAdapterTaskConsumer
    from worker.runtime.workflow_hub_gateway import HttpWorkflowHubDecisionClient, HubExecutionAuthorizationAdapter

    bootstrap = wait_json(HUB + '/fixture/bootstrap')
    root = Path('/tmp/pi-chain-worker')
    root.mkdir(mode=0o700)
    private = root / 'private'
    private.mkdir(mode=0o700)
    token = private / 'hub-token'
    token.write_text(TOKEN)
    token.chmod(0o400)
    keys = private / 'verification.json'
    keys.write_text(json.dumps(bootstrap['verification']))
    keys.chmod(0o400)
    os.environ.update(ANANTA_WORKFLOW_HUB_URL=HUB, ANANTA_WORKFLOW_HUB_TOKEN_FILE=str(token),
        ANANTA_WORKFLOW_AUTH_VERIFICATION_KEYRING_FILE=str(keys),
        ANANTA_NATIVE_RESULT_STORE_PATH=str(private / 'results.sqlite3'))
    client = HttpWorkflowHubDecisionClient(hub_url=HUB, bearer_token=TOKEN,
        worker_id='worker-1', worker_url='http://worker-1:5000')
    context = ProviderInvocationContext.from_value(bootstrap['context'])
    artifact_output = os.environ.get('ANANTA_TEST_PI_CHAIN_ANSWER_ARTIFACT') == '1'
    sdk = Path('/opt/ananta/cli-backends/pi/0.85.1/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js')
    argv = pi_sdk_command(str(sdk), path_environment='/usr/bin:/usr/local/bin')
    runtime = {'installed': True, 'status': 'ready', 'version': '0.85.1', 'binary_path': str(sdk),
               'version_probe': {'rc': 0, 'stdout': '0.85.1', 'stderr': ''}}

    class DiagnosticProcess(BoundedCodingAgentProcess):
        def run(self, *args, **kwargs):
            try:
                result = super().run(*args, **kwargs)
            except Exception as exc:
                message = str(exc).replace(TOKEN, '[redacted]').replace('synthetic-private-key', '[redacted]')
                print('PI_CONTAINER_PROCESS_EXCEPTION=' + json.dumps({
                    'type': type(exc).__name__, 'message': message[:1024]}), flush=True)
                raise
            if result.return_code:
                print('PI_CONTAINER_PROCESS_FAILURE=' + json.dumps({
                    'return_code': result.return_code, 'reason_code': result.reason_code,
                    'stderr': result.stderr[:1024]}), flush=True)
            for line in result.stdout.splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                message = event.get('message', {})
                if isinstance(message, dict) and message.get('stopReason') == 'error':
                    print('PI_CONTAINER_SDK_FAILURE=' + json.dumps({
                        'error': str(message.get('errorMessage', ''))[:1024]}), flush=True)
            return result

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr('agent.cli_backends.pi_configuration.pi_context_window', lambda target: 8192)
        adapter, _, _, _, _ = native_setup(root,
            mutate=artifact_command if artifact_output else lambda command: command,
            artifact_publisher=PiAnswerArtifactPublisher(publisher_factory=configured_pi_answer_publisher)
                if artifact_output else None,
            hub_composition=lambda **values: (client, context, []),
            runner=DiagnosticProcess(launch_context=pi_execution_launch),
            context_reader_factory=HubPiTaskContextReader, evidence_reader_factory=HubPiEvidenceReader,
            provider_options={'runtime_probe': lambda: runtime, 'command_builder': lambda target: argv,
                              'environment': {'PATH': '/usr/bin:/usr/local/bin'}})
        application = app()
        application.extensions['workflow_adapter_task_consumer'] = WorkflowAdapterTaskConsumer(
            authorization=HubExecutionAuthorizationAdapter(client), native_adapter=adapter)

        @application.get('/fixture/ready')
        def ready():
            return jsonify(ready=True)

        application.run(host='0.0.0.0', port=5000, use_reloader=False)


if __name__ == '__main__':
    {'hub': hub, 'worker': worker}[os.environ['ROLE']]()
