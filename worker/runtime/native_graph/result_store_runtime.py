"""Per-Worker application composition of its explicitly configured private store."""

import os

from flask import current_app

from ananta_contracts.native_result_maintenance import (
    MAINTENANCE_TASK_KIND,
    RESULT_STORE_BINDING_LIMIT,
    maintenance_task_owner,
    require_result_store_identifier,
)
from worker.runtime.native_graph.result_store import SQLiteNativeResultStore


def configured_native_result_store(*, expire_on_open=True, binding_id=None):
    key = "native_result_store"
    registered = current_app.extensions.get('workflow_adapter_worker_registration') or {}
    current_binding = (registered.get('execution_limits') or {}).get(RESULT_STORE_BINDING_LIMIT)
    if current_binding is not None:
        require_result_store_identifier(current_binding)
    if binding_id is not None and binding_id != current_binding:
        raise ValueError('native_result_store_binding_mismatch')
    if key in current_app.extensions and current_app.extensions.get('native_result_store_binding') != current_binding:
        raise ValueError('native_result_store_binding_changed')
    if key not in current_app.extensions:
        path = (current_app.config.get("ANANTA_NATIVE_RESULT_STORE_PATH")
                or os.environ.get("ANANTA_NATIVE_RESULT_STORE_PATH"))
        if not path:
            raise ValueError("native_result_store_configuration_required")
        options = {} if expire_on_open else {'expire_on_open': False}
        current_app.extensions[key] = SQLiteNativeResultStore(path, store_binding_id=current_binding, **options)
        current_app.extensions['native_result_store_binding'] = current_binding
    return current_app.extensions[key]


def configured_native_result_store_for_task(task):
    # A scoped owner-operation must not run global opportunistic housekeeping
    # before its delegated scope has been checked by the Native handler.
    if task.get('task_kind') == MAINTENANCE_TASK_KIND:
        owner = maintenance_task_owner(task)
        return configured_native_result_store(expire_on_open=False,
            binding_id=owner['store_binding_id'] if owner is not None else None)
    return configured_native_result_store()
