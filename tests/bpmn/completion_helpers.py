"""Synthetic component harness: real SQL stores/Hub/Worker, test queue only.

The replayable queue deliberately keeps results available after polling, as a
durable inbox would. It is not a production queue or a container e2e claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlmodel import SQLModel, create_engine

from agent.db_models.workflow_runtime import WorkflowAuthorizationGrantDB
from agent.services.native_graph_orchestration_service import NativeGraphOrchestrator, NativeGraphRequest
from agent.services.workflow_authorization_grant_service import SQLAlchemyWorkflowAuthorizationGrantService
from agent.services.workflow_runtime import (
    AuthorizationVerifier,
    HmacKeyRing,
    InMemoryReplayNonceStore,
    InMemorySideEffectLedger,
    WorkflowCommandVerifier,
)
from agent.services.workflow_runtime.native_graph_ports import HubTaskSubmission
from agent.services.workflow_runtime.ownership import SQLiteExecutionOwnershipStore
from agent.services.workflow_runtime.persistence import SQLiteCheckpointStore, SQLiteEventStore
from tests.bpmn.test_execution_admission import diagram
from tests.bpmn.test_execution_recovery import advance_bounded
from tests.bpmn.test_execution_routing import compile_request, execution_plan
from tests.test_native_graph_runtime import AllowPolicy, DeterministicHandler, ImmediateHubQueue
from worker.runtime.native_graph import HubTaskReceipt, NativeDelegatedNodeRuntime


class ReplayableWorkerQueue(ImmediateHubQueue):
    """Idempotent synthetic delivery with real delegated Worker validation."""

    def submit(self, command):
        for index, previous in enumerate(self.submissions, start=1):
            if previous.command_id == command.command_id:
                assert previous == command, "A dispatch retry changed its bound command"
                return HubTaskReceipt(f"hub-task-{index}", command.command_id, True)
        return super().submit(command)

    def submit_fenced(self, command, *, lease):
        """Synthetic in-memory admission serialized with the SQL lease writer.

        This fixture does not offer durable task ingestion. Production must
        supply its own same-transaction recipient or refuse fenced submission.
        """
        from agent.services.workflow_runtime.lease_fencing import validate_sqlite_lease

        store = lease.store
        with store._lock:
            store._begin()
            try:
                validate_sqlite_lease(store._connection, lease, command)
                receipt = self.submit(command)
                store._commit()
                return receipt
            except BaseException:
                store._rollback()
                raise

    def poll(self, *, tenant_id, run_id, hub_task_ids):
        return tuple(self.results[key] for key in hub_task_ids if key in self.results)

    def get_submission(self, *, command_id, tenant_id, run_id):
        for index, command in enumerate(self.submissions, start=1):
            if command.command_id == command_id:
                assert (command.tenant_id, command.run_id) == (tenant_id, run_id)
                return HubTaskSubmission(command, HubTaskReceipt(f"hub-task-{index}", command_id, True))
        return None


def linear_request(*, user_task=False, run_id="synthetic-completion"):
    kind = "userTask" if user_task else "serviceTask"
    xml = diagram(
        f'<startEvent id="start"/><{kind} id="work"/>'
        '<serviceTask id="after"/><endEvent id="end"/>'
        '<sequenceFlow id="begin" sourceRef="start" targetRef="work"/>'
        '<sequenceFlow id="next" sourceRef="work" targetRef="after"/>'
        '<sequenceFlow id="finish" sourceRef="after" targetRef="end"/>'
    )
    return NativeGraphRequest(execution_plan(compile_request(xml)), run_id, "synthetic-control")


@dataclass
class CompletionHarness:
    directory: Path
    now: float = 100.0
    connections: list = field(default_factory=list)

    def __post_init__(self):
        self.keys = HmacKeyRing({"synthetic-completion": "k" * 32}, active_key_id="synthetic-completion")
        self.handler = DeterministicHandler()
        self.ledger = InMemorySideEffectLedger()
        self.policy = AllowPolicy()
        self.grant_database = create_engine(f"sqlite:///{self.directory / 'grants.sqlite'}")
        SQLModel.metadata.create_all(self.grant_database, tables=[WorkflowAuthorizationGrantDB.__table__])
        self.grants = SQLAlchemyWorkflowAuthorizationGrantService(self.grant_database, clock=lambda: self.now)
        worker = NativeDelegatedNodeRuntime(
            handler=self.handler,
            authorization_verifier=AuthorizationVerifier(self.keys, InMemoryReplayNonceStore(clock=lambda: self.now)),
            policy=self.policy,
            capabilities=frozenset(
                {
                    "approval",
                    "bounded_parallel",
                    "deterministic_merge",
                    "retrieval",
                    "structured_output",
                    "tool_calling",
                }
            ),
            ledger=self.ledger,
            hub_revalidator=self.grants,
            clock=lambda: self.now,
        )
        self.queue = ReplayableWorkerQueue(worker)
        self.restart()

    def restart(self):
        self.stores = {
            "checkpoints": SQLiteCheckpointStore(self.directory / "checkpoints.sqlite"),
            "events": SQLiteEventStore(self.directory / "checkpoints.sqlite"),
            "ownership": SQLiteExecutionOwnershipStore(self.directory / "checkpoints.sqlite"),
        }
        self.connections.extend(self.stores.values())
        self.hub = NativeGraphOrchestrator(
            queue=self.queue,
            key_ring=self.keys,
            ledger=self.ledger,
            authorization_grants=self.grants,
            policy=self.policy,
            clock=lambda: self.now,
            command_verifier=WorkflowCommandVerifier(self.keys, InMemoryReplayNonceStore(clock=lambda: self.now)),
            **self.stores,
        )
        return self.hub

    def dispatch_first(self, request):
        result = self.hub.start(request)
        for _ in range(8):
            if self.queue.submissions:
                assert [item.node.node_id for item in self.queue.submissions] == ["work"]
                return result
            result = self.hub.advance(request)
        pytest.fail("First delegated task did not appear within eight Hub ticks")

    def finish(self, request):
        return advance_bounded(self.hub, request, self.hub.inspect(request))

    def close(self):
        for store in reversed(self.connections):
            store.close()
        self.grant_database.dispose()


@pytest.fixture
def harness(tmp_path):
    value = CompletionHarness(tmp_path)
    try:
        yield value
    finally:
        value.close()


class SimulatedHubCrash(BaseException):
    """A process crash must escape application-level Exception handlers."""


def crash_once(monkeypatch, target, method, *, after, predicate=lambda *args, **kwargs: True):
    operation = getattr(target, method)
    injected = False

    def invoke(*args, **kwargs):
        nonlocal injected
        if injected or not predicate(*args, **kwargs):
            return operation(*args, **kwargs)
        injected = True
        if after:
            operation(*args, **kwargs)
        raise SimulatedHubCrash(f"{'after' if after else 'before'} {method}")

    monkeypatch.setattr(target, method, invoke)
