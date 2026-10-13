"""Closed Hub permits for compacting result slots without removing replay denial."""

import math
import re
from copy import deepcopy

from ananta_contracts.native_context_bundle import native_context_digest
from ananta_contracts.native_result_maintenance import maintenance_input
from ananta_contracts.native_task_execution import native_execution_request

RETIREMENT_SCHEMA = 'ananta.native-result-retirement.v1'
RETIREMENT_CAPABILITY = 'workflow.result_retirement'
RETIREMENT_TOOL = 'native.result.retire'
RETIREMENT_CONTEXT_FIELD = 'native_result_retirement_manifest'
MAX_RETIREMENT_ENTRIES = 128  # Result receipts repeat the closed output in bounded transport envelopes.
_FIELDS = {'schema', 'tenant_id', 'project_id', 'maintenance_task_id', 'command_digest',
           'owner', 'expires_at', 'entries'}


def retirement_payload(value, *, task, owner, now):
    if not isinstance(value, dict) or set(value) != _FIELDS or value['schema'] != RETIREMENT_SCHEMA:
        raise ValueError('native_result_retirement_manifest_invalid')
    task = native_execution_request(task)['task']
    command = task['worker_execution_context']['native_node_command']
    inputs = command.get('input_data')
    if not isinstance(inputs, dict):
        raise ValueError('native_result_retirement_binding_denied')
    batch, bound_owner = maintenance_input(inputs.get('workflow_input'))
    authorization = command.get('authorization') or {}
    node = command.get('node') or {}
    if (not isinstance(authorization, dict)
            or any(not isinstance(container.get(field), (list, tuple))
                   for container, field in ((node, 'required_capabilities'), (node, 'allowed_tools'),
                                            (authorization, 'allowed_tools')))):
        raise ValueError('native_result_retirement_binding_denied')
    inputs = inputs['workflow_input']
    if (task.get('task_kind') != 'native_result_maintenance' or inputs.get('retire_expired') is not True
            or bound_owner != owner or value['owner'] != owner
            or RETIREMENT_CAPABILITY not in node.get('required_capabilities', ())
            or RETIREMENT_TOOL not in node.get('allowed_tools', ())
            or RETIREMENT_TOOL not in authorization.get('allowed_tools', ())
            or value['tenant_id'] != task.get('tenant_id')
            or not isinstance(task.get('project_id'), str) or not task['project_id']
            or value['project_id'] != task['project_id']
            or value['maintenance_task_id'] != task.get('id')
            or value['command_digest'] != native_context_digest(command)):
        raise ValueError('native_result_retirement_binding_denied')
    expiry, grant_expiry = value['expires_at'], authorization.get('expires_at')
    if (any(type(item) not in (int, float) or not math.isfinite(item)
            for item in (now, expiry, grant_expiry))
            or not now < expiry <= min(now + 60, grant_expiry)):
        raise ValueError('native_result_retirement_expired')
    entries = value['entries']
    if not isinstance(entries, list) or len(entries) > min(batch, MAX_RETIREMENT_ENTRIES):
        raise ValueError('native_result_retirement_entries_invalid')
    identities = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'task_id', 'binding_digest'}
                or not isinstance(entry['task_id'], str) or not 0 < len(entry['task_id']) <= 200
                or len(entry['task_id'].encode('utf-8')) > 200
                or any(ord(char) < 33 for char in entry['task_id'])
                or entry['task_id'] == task['id'] or entry['task_id'] in identities
                or not isinstance(entry['binding_digest'], str)
                or re.fullmatch('[a-f0-9]{64}', entry['binding_digest']) is None):
            raise ValueError('native_result_retirement_entries_invalid')
        identities.add(entry['task_id'])
    return deepcopy(value)


def verify_retirement_manifest(value, *, task, owner, key_ring, now):
    if not isinstance(value, dict) or set(value) != _FIELDS | {'key_id', 'signature'}:
        raise ValueError('native_result_retirement_signature_required')
    payload = retirement_payload({name: value[name] for name in _FIELDS}, task=task, owner=owner, now=now)
    if key_ring is None or key_ring.signature_algorithm != 'ed25519':
        raise ValueError('native_result_retirement_public_verification_required')
    key_ring.verify(namespace=RETIREMENT_SCHEMA, payload=payload, key_id=value['key_id'],
                    signature=value['signature'], contract_id=task['worker_execution_context']
                    ['native_node_command']['command_id'])
    return payload
