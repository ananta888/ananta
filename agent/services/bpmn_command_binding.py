"""Closed execution preconditions alongside compatibility public status identities."""

import re


def project_bpmn_command_binding(raw, *, binding):
    """Hints never authorize execution; Hub ingress revalidates the signed checkpoint."""
    if 'command_binding' not in raw:
        return None
    value = raw['command_binding']
    fields = {'schema', 'expected_revision', 'run_id', 'plan_hash', 'checkpoint_ref'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('workflow_runtime_command_binding_invalid')
    revision = value['expected_revision']
    checkpoint = value['checkpoint_ref']
    if (
        value['schema'] != 'ananta.workflow_command_binding.v1'
        or type(revision) is not int or revision < 1 or revision != raw.get('revision')
        or value['run_id'] != binding.run_id or value['plan_hash'] != binding.plan_hash
        or not isinstance(checkpoint, str) or re.fullmatch(r'wfc-[0-9a-f]{32}', checkpoint) is None
        or raw.get('checkpoint_ref') not in (checkpoint, f'local:{binding.workflow_id}:{revision}')
    ):
        raise ValueError('workflow_runtime_command_binding_mismatch')
    return dict(value)
