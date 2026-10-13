"""Test composition of real Hub services; the Hub alone dispatches and accepts results."""

from __future__ import annotations

import base64
import json
import os
import secrets
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from bpmn_container.transport import WORKER_ID, WORKER_URL, request_json, require_isolation, require_token
from flask import Flask, jsonify, request
from werkzeug.serving import make_server


class AcceptanceHub:
    """Own test bootstrap and compose production ports without replacing them."""

    def __init__(self):
        require_isolation("hub")
        from sqlmodel import SQLModel

        import agent.db_models  # noqa: F401 - register production schema
        from agent.database import engine
        from agent.repository import agent_repo, task_repo
        from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator
        from agent.services.native_graph_production_composition import HubGovernedNativeControlPolicy
        from agent.services.native_graph_task_queue_adapter import build_native_graph_task_queue_adapter
        from agent.services.workflow_authorization_grant_service import SQLAlchemyWorkflowAuthorizationGrantService
        from agent.services.workflow_control_persistence import SQLAlchemyWorkflowCommandReplayNonceStore
        from agent.services.workflow_runtime import (
            AuthorizationVerifier,
            SQLAlchemyCheckpointStore,
            SQLAlchemyEventStore,
            SQLAlchemyExecutionOwnershipStore,
            SQLAlchemySideEffectLedger,
            WorkflowCommandVerifier,
        )
        from agent.services.workflow_worker_assignment_runtime import get_workflow_worker_assignment_store
        from agent.services.workflow_worker_gateway_service import WorkflowWorkerGatewayService

        SQLModel.metadata.create_all(engine)
        self.engine = engine
        self.tasks = task_repo
        self.agents = agent_repo
        self.app = Flask(__name__)
        self.app.config.update(
            MAX_CONTENT_LENGTH=262_144, AGENT_CONFIG={}, ROLE='hub',
            ANANTA_WORKFLOW_REQUIRE_REGISTERED_WORKER_AUTH=True
        )
        signing = {"schema": "ananta.workflow-auth-signing-keyring.v1", "algorithm": "ed25519",
                   "active_key_id": "ephemeral-hub",
                   "private_keys": {"ephemeral-hub": base64.b64encode(secrets.token_bytes(32)).decode()}}
        signing_path = Path("/sandbox/secrets/hub-signing.json")
        with signing_path.open("x", encoding="utf-8") as output:
            json.dump(signing, output)
        signing_path.chmod(0o600)
        os.environ["ANANTA_WORKFLOW_AUTH_SIGNING_KEYRING_FILE"] = str(signing_path)
        # Use the production composition lookup, including Task completion policy.
        from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring

        self.keys = get_workflow_authorization_key_ring()
        self._register_worker()
        self.assignments = get_workflow_worker_assignment_store()
        self.grants = SQLAlchemyWorkflowAuthorizationGrantService(engine)
        self.events = SQLAlchemyEventStore(engine)
        self.ownership = SQLAlchemyExecutionOwnershipStore(engine)
        self.ledger = SQLAlchemySideEffectLedger(engine)
        nonces = SQLAlchemyWorkflowCommandReplayNonceStore(engine)
        self.queue = build_native_graph_task_queue_adapter()
        self.orchestrator = NativeGraphOrchestrator(
            queue=self.queue,
            checkpoints=SQLAlchemyCheckpointStore(engine),
            events=self.events,
            ownership=self.ownership,
            ledger=self.ledger,
            key_ring=self.keys,
            command_verifier=WorkflowCommandVerifier(self.keys, nonces),
            policy=HubGovernedNativeControlPolicy(),
            authorization_grants=self.grants,
        )
        self.gateway = WorkflowWorkerGatewayService(
            authorization=AuthorizationVerifier(self.keys, nonces),
            ownership=self.ownership,
            ledger=self.ledger,
            events=self.events,
            assignments=self.assignments,
            authorization_revalidator=self.grants,
        )
        self.authorizations = []
        self._routes()
        from bpmn_container.artifacts import compose_hub_artifacts

        self.artifacts = compose_hub_artifacts(self)
        from bpmn_container.public_api import PublicApi

        self.public = PublicApi(self)
        from bpmn_container.browser_hub import BrowserGate

        self.browser = BrowserGate(self)
        self.server = make_server("0.0.0.0", 8080, self.app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.pending = {}
        self.dispatched = set()

    def _register_worker(self):
        from bpmn_container.registration import register_worker

        self.worker = register_worker(self.app, self.agents)

    def _routes(self):
        from agent.services.workflow_worker_gateway_service import WorkflowWorkerGatewayError
        from agent.services.workflow_worker_service_auth import (
            WORKFLOW_WORKER_COMMAND_SCOPE,
            WorkflowWorkerAuthDenied,
            authenticate_registered_workflow_worker,
        )

        @self.app.get("/verification-keyring")
        def keys():
            require_token("BPMN_WORKER_TOKEN")
            return jsonify(self.keys.verification_mapping())

        @self.app.post("/api/internal/workflow-runtime/worker-commands")
        def command():
            try:
                identity = authenticate_registered_workflow_worker(
                    request.headers.get("Authorization", "").removeprefix("Bearer "),
                    required_scope=WORKFLOW_WORKER_COMMAND_SCOPE,
                    claimed_worker_id=request.headers.get("X-Ananta-Worker-ID", ""),
                    claimed_worker_url=request.headers.get("X-Ananta-Worker-URL", ""),
                    registered_agents=self.agents.get_all(),
                    config=self.app.config,
                )
                body = request.get_json()
                result = self.gateway.execute(
                    body, authenticated_worker_id=identity.worker_id, authenticated_worker_url=identity.worker_url
                )
                self.authorizations.append(
                    {
                        "run_id": body["binding"]["run_id"],
                        "node": body["binding"]["step_id"],
                        "allowed": result.get("allowed") is True,
                    }
                )
                return jsonify(result)
            except (WorkflowWorkerAuthDenied, WorkflowWorkerGatewayError) as exc:
                print("HUB_COMMAND_DENIED=" + exc.reason_code, flush=True)
                return jsonify(reason_code=exc.reason_code), exc.status_code

    def dispatch_ready(self, run_id: str):
        from agent.services.task_queue_service import get_task_queue_service
        from agent.services.task_runtime_service import update_local_task_status
        from agent.services.workflow_worker_assignment_runtime import bind_dispatched_workflow_task

        for entry in get_task_queue_service().get_dispatch_queue():
            task = self.tasks.get_by_id(entry["task_id"])
            command = (task.worker_execution_context or {}).get("native_node_command", {})
            if command.get("run_id") != run_id or task.id in self.dispatched:
                continue
            bind_dispatched_workflow_task(task=task, worker=self.worker, config=self.app.config)
            update_local_task_status(task.id, "assigned", assigned_agent_url=WORKER_URL)
            update_local_task_status(task.id, "in_progress")
            task = self.tasks.get_by_id(task.id)
            self.dispatched.add(task.id)
            self.pending[task.id] = self.pool.submit(
                request_json,
                WORKER_URL + "/execute",
                token=os.environ["BPMN_DISPATCH_TOKEN"],
                payload=task.model_dump(mode="json"),
            )

    def collect(self, *, transform_result=None):
        from agent.services._task_scoped_forwarding import persist_forwarded_execution

        for task_id, future in tuple(self.pending.items()):
            if not future.done():
                continue
            del self.pending[task_id]
            response = future.result()
            if transform_result is not None:
                response = transform_result(response)
            persist_forwarded_execution(
                tid=task_id,
                response=response,
                task=self.tasks.get_by_id(task_id).model_dump(),
                request_data=SimpleNamespace(command=None),
            )

    def tasks_for(self, run_id):
        return [
            task
            for task in self.tasks.get_all()
            if (task.worker_execution_context or {}).get("native_node_command", {}).get("run_id") == run_id
        ]

    def observations(self, run_id):
        from agent.services.workflow_runtime._serialization import sha256_json
        from agent.services.workflow_runtime.native_graph_contracts import NativeNodeResult

        observations = []
        for task in self.tasks_for(run_id):
            raw = (task.verification_status or {}).get("native_node_result", {})
            command = task.worker_execution_context["native_node_command"]
            node = command["node"]["node_id"]
            assignment = self.assignments.get(tenant_id=command["tenant_id"], run_id=run_id, step_id=node)
            assert assignment is not None and assignment.hub_task_id == task.id
            assert assignment.worker_id == WORKER_ID
            assert raw["hub_task_id"] == task.id and raw["attempt_id"] == assignment.attempt_id
            assert raw["fencing_token"] == assignment.fencing_token
            if task.status == "completed":
                owner = self.ownership.get(tenant_id=command["tenant_id"], run_id=run_id, step_id=node)
                result = NativeNodeResult.from_mapping(raw)
                expected_ack = "bpmn-result:" + sha256_json({**result.to_dict(), "output_data": result.output_data})
                assert owner.result_ack_key == expected_ack, "bpmn_result_content_ack_mismatch"
                assert raw["output_data"]["worker_hostname"] != socket.gethostname()
            observations.append(
                {
                    "task_id": task.id,
                    "node": node,
                    "status": task.status,
                    "assignment_revision": assignment.revision,
                    "result": raw.get("output_data", {}),
                    "reason_code": raw.get("reason_code", ""),
                }
            )
        return observations

    def close(self):
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.public.close()
        self.artifacts.close()
        self.engine.dispose()
