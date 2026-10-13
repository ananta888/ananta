"""Current Hub authorization before maintenance consumes private-store capacity."""

from worker.runtime.native_graph.contracts import NativeNodeCommand
from worker.runtime.native_graph.worker_binding import native_command_worker_binding
from worker.runtime.workflow_adapter_task_consumer import (
    NATIVE_GRAPH_RUNTIME_PATH,
    NATIVE_GRAPH_WORKER_CONTEXT_SCHEMA,
    ExecutionAuthorizationDecision,
    HubExecutionAuthorizationPort,
)


class NativeExecutionAdmission:
    """Read permission only: no adapter, task queue, execution or nonce store."""

    def __init__(self, *, authorization: HubExecutionAuthorizationPort):
        self._authorization = authorization

    def authorize(self, task):
        try:
            context = task.get('worker_execution_context') or {}
            if ((context.get('schema'), context.get('runtime_path')) != (
                    NATIVE_GRAPH_WORKER_CONTEXT_SCHEMA, NATIVE_GRAPH_RUNTIME_PATH)):
                return False
            command = NativeNodeCommand.from_mapping(dict(context.get('native_node_command') or {}))
            if command.tenant_id != task.get('tenant_id') or command.node.task_kind != task.get('task_kind'):
                return False
            decision = self._authorization.authorize(binding=native_command_worker_binding(command),
                adapter_kind='native', attempt_id=command.attempt_id, fencing_token=command.fencing_token)
            return isinstance(decision, ExecutionAuthorizationDecision) and decision.allowed is True
        except Exception:
            # An unavailable/malformed Hub decision cannot allocate an extra slot.
            return False
