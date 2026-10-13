"""Native command projection for the existing read-only Hub decision port."""

from ananta_contracts.workflow_worker_gateway import WorkflowWorkerBinding


def native_command_worker_binding(command):
    return WorkflowWorkerBinding.from_mapping({
        'tenant_id': command.tenant_id, 'workflow_id': command.workflow_id, 'run_id': command.run_id,
        'step_id': command.node.node_id, 'plan_hash': command.plan_hash,
        'policy_version': command.policy_version, 'authorization_envelope': command.authorization.to_dict(),
    })
