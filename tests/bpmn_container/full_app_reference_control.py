"""Explicit synthetic headless policy commands for the real full Hub runtime."""

import time


def _resume(orchestrator, request, result, *, kind, step_id, payload):
    from agent.services.workflow_hub_task_gateway_runtime import get_workflow_authorization_key_ring
    from agent.services.workflow_runtime import SignedWorkflowCommand

    checkpoint = result.checkpoint
    command = SignedWorkflowCommand.issue(key_ring=get_workflow_authorization_key_ring(), command_type=kind,
        tenant_id=checkpoint.tenant_id, workflow_id=checkpoint.workflow_id, run_id=checkpoint.run_id,
        step_id=step_id, checkpoint_id=checkpoint.checkpoint_id, expected_revision=checkpoint.revision,
        plan_hash=checkpoint.plan_hash, policy_version=checkpoint.policy_version,
        actor_id='synthetic-full-app-policy', actor_roles=('operator',), payload=payload, now=time.time())
    return orchestrator.resume(request, command=command)


def approval_control(decision):
    def control(orchestrator, request, result):
        if result.status == 'waiting_for_approval':
            return _resume(orchestrator, request, result, kind=decision, step_id='review',
                           payload={'policy_fixture': 'explicit-synthetic-' + decision})
        return result
    return control


def message_control(orchestrator, request, result):
    intent = result.checkpoint.state.runtime_metadata.get('bpmn_waits', {}).get('catch', {})
    if not intent.get('wakeup_id') or intent.get('status') in {'consumed', 'completed'}:
        return result
    definition = next(node for node in request.plan.nodes if node.node_id == 'catch').metadata['bpmn_wait']
    now = time.time()
    return _resume(orchestrator, request, result, kind='bpmn_message', step_id='catch', payload={
        'activation_id': intent['activation_id'], 'message_id': 'synthetic-full-app-message',
        'name': definition['name'], 'correlation_key': definition['correlation_key'],
        'schema_id': definition['schema_id'], 'sent_at': now, 'expires_at': now + 15, 'payload': {'value': 7},
    })
