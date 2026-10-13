"""One Hub-delegated owner-store cleanup; no timer, scheduling or slot deletion."""

import time
import uuid
from typing import Protocol

from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_result_maintenance import (
    MAINTENANCE_CAPABILITY as MAINTENANCE_CAPABILITY,
)
from ananta_contracts.native_result_maintenance import (
    MAINTENANCE_TASK_KIND as MAINTENANCE_TASK_KIND,
)
from ananta_contracts.native_result_maintenance import (
    MAINTENANCE_TOOL as MAINTENANCE_TOOL,
)
from ananta_contracts.native_result_maintenance import maintenance_input
from ananta_contracts.native_result_retirement import (
    RETIREMENT_CAPABILITY,
    RETIREMENT_CONTEXT_FIELD,
    RETIREMENT_TOOL,
    verify_retirement_manifest,
)
from worker.runtime.native_graph.contracts import NativeNodeResult
from worker.runtime.native_graph.result_store_runtime import configured_native_result_store


class ResultExpiryPort(Protocol):
    def expire_results(self, *, batch_size: int, tenant_id: str, project_id: str) -> int: ...


class MaintenanceTaskScopePort(Protocol):
    def task_snapshot(self, *, hub_task_id: str) -> dict: ...
    def revalidate(self, envelope) -> bool: ...


def configured_maintenance_result_store(*, binding_id=None):
    return configured_native_result_store(expire_on_open=False, binding_id=binding_id)


class NativeResultMaintenanceHandler:
    def __init__(self, *, scope: MaintenanceTaskScopePort, store_factory=configured_maintenance_result_store,
                 store_binding_id=None, retirement_keys=None, clock=time.time):
        self._scope, self._store_factory = scope, store_factory
        self._store_binding_id = store_binding_id
        self._retirement_keys, self._clock = retirement_keys, clock

    def execute(self, command, *, hub_task_id):
        inputs = command.input_data
        if (not isinstance(inputs, dict)
                or set(inputs) != {'workflow_input', 'dependency_results', 'requested_artifacts'}
                or inputs['dependency_results'] != {} or inputs['requested_artifacts'] != []):
            raise ValueError('native_result_maintenance_scope_invalid')
        batch = inputs['workflow_input']
        batch_size, owner = maintenance_input(batch)
        retire = batch.get('retire_expired') is True
        if retire and self._retirement_keys is None:
            raise ValueError('native_result_retirement_not_composed')
        tools = (MAINTENANCE_TOOL, RETIREMENT_TOOL) if retire else (MAINTENANCE_TOOL,)
        if (command.node.task_kind != MAINTENANCE_TASK_KIND
                or MAINTENANCE_CAPABILITY not in command.node.required_capabilities
                or retire and RETIREMENT_CAPABILITY not in command.node.required_capabilities
                or command.node.allowed_tools != tools
                or any(tool not in command.authorization.allowed_tools for tool in tools)
                or command.node.side_effect_class != 'idempotent_write' or not command.operation_id
                or command.node.input_artifacts or command.node.output_artifacts or command.artifact_refs
                ):
            raise ValueError('native_result_maintenance_scope_invalid')
        if owner is not None and owner['store_binding_id'] != self._store_binding_id:
            raise ValueError('native_result_store_binding_mismatch')
        task = self._scope.task_snapshot(hub_task_id=hub_task_id)
        raw = (task.get('worker_execution_context') or {}).get('native_node_command')
        if (task.get('id') != hub_task_id or task.get('tenant_id') != command.tenant_id
                or not isinstance(task.get('project_id'), str) or not task['project_id']
                or native_context_digest(raw) != native_context_digest(command.to_dict())):
            raise ValueError('native_result_maintenance_task_binding_invalid')
        if self._scope.revalidate(command.authorization) is not True:
            raise ValueError('native_result_maintenance_authority_denied')
        manifest, keys = None, None
        if retire:
            manifest = (task.get('worker_execution_context') or {}).get(RETIREMENT_CONTEXT_FIELD)
            keys = self._retirement_keys()
            verify_retirement_manifest(manifest, task=task, owner=owner, key_ring=keys, now=self._clock())
        store = self._store_factory()
        scrubbed = store.expire_results(batch_size=batch_size,
            tenant_id=command.tenant_id, project_id=task['project_id'])
        if type(scrubbed) is not int or not 0 <= scrubbed <= batch_size:
            raise ValueError('native_result_maintenance_outcome_invalid')
        if self._scope.revalidate(command.authorization) is not True:
            raise ValueError('native_result_maintenance_authority_denied')
        output = {'scrubbed_results': scrubbed, 'replay_tombstones_preserved': True}
        if retire:
            output.update(store.compact_results_receipt(manifest, task=task, owner=owner, key_ring=keys))
            if self._scope.revalidate(command.authorization) is not True:
                raise ValueError('native_result_maintenance_authority_denied')
        return NativeNodeResult(result_id='nres-' + uuid.uuid4().hex,
            command_id=command.command_id, hub_task_id=hub_task_id, tenant_id=command.tenant_id,
            workflow_id=command.workflow_id, run_id=command.run_id, node_id=command.node.node_id,
            attempt_id=command.attempt_id, fencing_token=command.fencing_token, status='completed',
            output_data=output)
