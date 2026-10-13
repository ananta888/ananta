"""Execution-only consumer of a currently Hub-verified Pi assignment."""

import copy
from typing import Protocol

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_task_execution import native_execution_request
from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding
from worker.runtime.native_graph.contracts import NativeNodeCommand
from worker.runtime.native_graph.pi_evidence_scope import bind_pi_evidence


class PiEvidenceReadPort(Protocol):
    def read(self, *, task, command) -> dict:
        """Return the exact current Registry projection through authenticated Hub transport."""
        ...


class HubPiEvidenceReader:
    def __init__(self, client):
        self._client = client

    def read(self, *, task, command):
        binding = WorkflowWorkerBinding(tenant_id=command.tenant_id, workflow_id=command.workflow_id,
            run_id=command.run_id, step_id=command.node.node_id, plan_hash=command.plan_hash,
            policy_version=command.policy_version, authorization_envelope=command.authorization.to_dict())
        return validate_hub_evidence_assignment(self._client.command('pi_evidence_read',
            binding=binding.to_dict(), hub_task_id=task['id'], command_id=command.command_id,
            attempt_id=command.attempt_id, fencing_token=command.fencing_token,
            input_digest=native_context_digest(task['worker_execution_context'])))


class VerifiedPiEvidenceConsumer:
    def __init__(self, *, reader: PiEvidenceReadPort, execute):
        self._reader, self._execute = reader, execute

    def __call__(self, task, evidence_assignment):
        task = native_execution_request(task)['task']
        projection = validate_hub_evidence_assignment(copy.deepcopy(evidence_assignment))
        command = NativeNodeCommand.from_mapping(task['worker_execution_context'].get('native_node_command', {}))
        if (task['task_kind'] != 'pi_coding_agent' or command.node.task_kind != 'pi_coding_agent'
                or command.tenant_id != task['tenant_id'] or projection['task_id'] != task['id']):
            raise ValueError('pi_worker_evidence_task_binding_denied')
        # The decision port cannot mutate the execution input or assigned scope.
        registered = validate_hub_evidence_assignment(
            self._reader.read(task=copy.deepcopy(task), command=copy.deepcopy(command)))
        if native_context_digest(registered) != native_context_digest(projection):
            raise ValueError('pi_worker_evidence_assignment_mismatch')
        with bind_pi_evidence(projection):
            return self._execute(task)
