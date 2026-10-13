"""Shared identifiers for a Hub-delegated finite private-result expiry batch."""

import re
from urllib.parse import urlsplit

MAINTENANCE_TASK_KIND = 'native_result_maintenance'
MAINTENANCE_CAPABILITY = 'workflow.result_maintenance'
MAINTENANCE_TOOL = 'native.result.expire'
RESULT_STORE_BINDING_LIMIT = 'native_result_store_binding_id'


def require_result_store_identifier(value):
    if not isinstance(value, str) or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is None:
        raise ValueError('native_result_store_binding_invalid')
    return value


def maintenance_input(value):
    """Closed operation inputs, optionally bound to one registered private store."""
    if (not isinstance(value, dict) or set(value) - {'batch_size', 'result_store_owner', 'retire_expired'}
            or 'batch_size' not in value
            or type(value['batch_size']) is not int or not 1 <= value['batch_size'] <= 1000):
        raise ValueError('native_result_maintenance_scope_invalid')
    if ('retire_expired' in value and (type(value['retire_expired']) is not bool
            or value['retire_expired'] and 'result_store_owner' not in value)):
        raise ValueError('native_result_maintenance_scope_invalid')
    owner = value.get('result_store_owner')
    if 'result_store_owner' in value:
        if not isinstance(owner, dict) or set(owner) != {'worker_id', 'worker_url', 'store_binding_id'}:
            raise ValueError('native_result_store_owner_invalid')
        require_result_store_identifier(owner['worker_id'])
        require_result_store_identifier(owner['store_binding_id'])
        url = owner['worker_url']
        if not isinstance(url, str) or len(url) > 2048 or any(char.isspace() for char in url):
            raise ValueError('native_result_store_owner_invalid')
        parsed = urlsplit(url)
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in {'', '/'} or parsed.query or parsed.fragment):
            raise ValueError('native_result_store_owner_invalid')
        # Accessing port validates its syntax and range.
        parsed.port
        owner = {**owner, 'worker_url': url.rstrip('/')}
    return value['batch_size'], owner


def maintenance_task_owner(task):
    if task.get('task_kind') != MAINTENANCE_TASK_KIND:
        return None
    command = (task.get('worker_execution_context') or {}).get('native_node_command') or {}
    inputs = command.get('input_data') or {}
    return maintenance_input(inputs.get('workflow_input'))[1]


def require_maintenance_recipient(task, *, worker_id, worker_url):
    owner = maintenance_task_owner(task)
    if owner is not None and (worker_id != owner['worker_id'] or not isinstance(worker_url, str)
                              or worker_url.rstrip('/') != owner['worker_url']):
        raise ValueError('native_result_store_recipient_mismatch')
