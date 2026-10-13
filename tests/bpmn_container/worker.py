"""Execution-only worker: production consumer/runtime with one deterministic handler."""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

from bpmn_container.transport import HUB_URL, WORKER_ID, WORKER_URL, request_json, require_isolation, require_token
from flask import Flask, jsonify, request


class DeterministicHandler:
    """Closed execution fixture; optional real worker-local artifact storage."""

    def __init__(self, artifact_store=None):
        self._releases = {}
        self._lock = threading.Lock()
        self._artifact_store = artifact_store
        self._tasks = {}

    def bind_task(self, task):
        from copy import deepcopy

        with self._lock:
            self._tasks[task["id"]] = deepcopy(task)

    def _release_for(self, task_id):
        with self._lock:
            return self._releases.setdefault(task_id, threading.Event())

    def release(self, task_id):
        self._release_for(task_id).set()

    def execute(self, command, *, hub_task_id):
        from worker.runtime.native_graph.contracts import NativeNodeResult

        started = time.time()
        isolation_observation = {}
        if os.environ.get("BPMN_TOOL_ISOLATION_ENABLED") == "1":
            from bpmn_container.tool_isolation import exercise_worker_store_isolation

            isolation_observation["tool_isolation"] = exercise_worker_store_isolation(hub_task_id)
        # The Hub fixture releases this task only after observing the first
        # sibling completion. This makes join assertions independent of CPU speed.
        if command.node.node_id == "slow" and not self._release_for(hub_task_id).wait(timeout=6):
            raise RuntimeError("bpmn_test_hub_release_timeout")
        artifact_refs, artifact_observation = {}, {}
        if command.node.node_id == "artifact_work":
            artifact_refs, artifact_observation = self._artifact_store.produce(command, self._tasks[hub_task_id])
        elif command.node.node_id == "artifact_consumer":
            artifact_observation = self._artifact_store.consume(command, self._tasks[hub_task_id])
        scoped = command.input_data.get("workflow_input", {})
        value = scoped.get("value")
        projected_output = {"value": value + 1} if type(value) is int else {}
        evidence = self._tasks[hub_task_id].get('worker_execution_context', {}).get('synthetic_hub_evidence_assignment')
        if evidence is not None:
            from ananta_contracts.hub_evidence import validate_hub_evidence_assignment

            evidence = validate_hub_evidence_assignment(evidence)
            assert evidence['task_id'] == hub_task_id and evidence['evidence_scope'] == 'test'
            projected_output['hub_test_evidence_assignment'] = evidence
        return NativeNodeResult(
            result_id="bpmn-result-" + command.command_id,
            command_id=command.command_id,
            hub_task_id=hub_task_id,
            tenant_id=command.tenant_id,
            workflow_id=command.workflow_id,
            run_id=command.run_id,
            node_id=command.node.node_id,
            attempt_id=command.attempt_id,
            fencing_token=command.fencing_token,
            status="completed",
            output_data={
                "node": command.node.node_id,
                "worker_hostname": socket.gethostname(),
                "started_at": started,
                "finished_at": time.time(),
                "synthetic": True,
                **projected_output,
                **artifact_observation,
                **isolation_observation,
            },
            artifact_refs=artifact_refs,
            budget_usage={"tokens": 0, "cost_micros": 0},
        )


def main() -> None:
    require_isolation("worker")
    from agent.services.workflow_runtime import AuthorizationVerifier, InMemoryReplayNonceStore
    from ananta_contracts.runtime_authorization_crypto import Ed25519VerificationKeyRing
    from worker.runtime.native_graph.composition import ConfiguredNativeNodePolicy
    from worker.runtime.native_graph.node_runtime import NativeDelegatedNodeRuntime
    from worker.runtime.native_graph.task_adapter import NativeGraphWorkerTaskAdapter
    from worker.runtime.workflow_adapter_task_consumer import WorkflowAdapterTaskConsumer
    from worker.runtime.workflow_adapter_task_execution import consume_delegated_workflow_task
    from worker.runtime.workflow_hub_gateway import HttpWorkflowHubDecisionClient, HubExecutionAuthorizationAdapter

    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=262_144, ROLE="worker")
    result_directory = (Path("/native-results") if os.environ.get("BPMN_WORKER_REDELIVERY_ENABLED") == "1"
                        else Path("/tmp/native-result-private"))
    result_directory.mkdir(mode=0o700, exist_ok=True)
    app.config["ANANTA_NATIVE_RESULT_STORE_PATH"] = str(result_directory / "results.sqlite3")
    initialization = threading.Lock()
    from bpmn_container.artifacts import prepare_artifact_store

    handler = DeterministicHandler(artifact_store=prepare_artifact_store())

    @app.get("/health")
    def health():
        return jsonify(role="worker", hostname=socket.gethostname())

    @app.post("/release")
    def release():
        require_token("BPMN_DISPATCH_TOKEN")
        task_id = request.get_json().get("task_id")
        if not isinstance(task_id, str) or not task_id.startswith("wfn-") or len(task_id) > 100:
            return jsonify(reason_code="test_task_id_invalid"), 422
        handler.release(task_id)
        return jsonify(released=True)

    def initialize():
        with initialization:
            if "workflow_adapter_task_consumer" not in app.extensions:
                public = request_json(HUB_URL + "/verification-keyring", token=os.environ["BPMN_WORKER_TOKEN"],
                                      headers={"X-Ananta-Worker-ID": WORKER_ID, "X-Ananta-Worker-URL": WORKER_URL})
                app.extensions["native_execution_verification"] = Ed25519VerificationKeyRing.from_mapping(public)
                client = HttpWorkflowHubDecisionClient(
                    hub_url=HUB_URL,
                    bearer_token=os.environ["BPMN_WORKER_TOKEN"],
                    worker_id=WORKER_ID,
                    worker_url=WORKER_URL,
                    timeout_seconds=5,
                )
                runtime = NativeDelegatedNodeRuntime(
                    handler=handler,
                    authorization_verifier=AuthorizationVerifier(
                        Ed25519VerificationKeyRing.from_mapping(public), InMemoryReplayNonceStore()
                    ),
                    policy=ConfiguredNativeNodePolicy(allowed_task_types=frozenset({"tool_task", "human_task"})),
                    capabilities=frozenset({"approval", "bounded_parallel", "bpmn_control_v1"}),
                )
                app.extensions["workflow_adapter_task_consumer"] = WorkflowAdapterTaskConsumer(
                    authorization=HubExecutionAuthorizationAdapter(client),
                    native_adapter=NativeGraphWorkerTaskAdapter(runtime),
                )
    def consume(task):
        initialize()
        handler.bind_task(task)
        result = consume_delegated_workflow_task(task)
        if (os.environ.get("BPMN_WORKER_LOSS_ENABLED") == "1"
                and os.environ.get("BPMN_WORKER_REDELIVERY_ENABLED") != "1"):
            # Dedicated isolated fault gate: real process/container termination
            # after execution, before any HTTP result reaches Hub admission.
            os._exit(73)
        return result

    def verification():
        initialize()
        return app.extensions["native_execution_verification"]

    from worker.runtime.native_graph.http_execution import create_native_execution_blueprint
    from worker.runtime.native_graph.result_store_runtime import configured_native_result_store

    def result_store():
        store = configured_native_result_store()
        if os.environ.get("BPMN_WORKER_REDELIVERY_ENABLED") != "1":
            return store

        class FaultAfterPublication:
            def reserve(self, task):
                store.reserve(task)

            def read(self, task):
                return store.read(task)

            def publish(self, task, result):
                store.publish(task, result)
                marker = result_directory / "fault-once"
                if not marker.exists():
                    marker.touch(mode=0o600)
                    os._exit(73)

        return FaultAfterPublication()

    app.register_blueprint(create_native_execution_blueprint(
        token_provider=lambda: os.environ["BPMN_WORKER_TOKEN"],
        recipient_provider=lambda: (WORKER_ID, WORKER_URL), verification_provider=verification, consumer=consume,
        result_store_provider=result_store,
    ))

    @app.post("/execute")
    def execute():
        require_token("BPMN_DISPATCH_TOKEN")
        result = consume(request.get_json())
        if result is None:
            return jsonify(reason_code="unsupported_task_contract"), 422
        return jsonify(result)

    app.run(host="0.0.0.0", port=8080, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
