"""Explicit opt-in for one delegated maintenance task within the Native adapter."""

from functools import partial

from ananta_contracts.native_result_maintenance import require_result_store_identifier
from ananta_contracts.native_result_retirement import RETIREMENT_CAPABILITY
from worker.runtime.native_graph.authorization import load_ed25519_verification_key_ring
from worker.runtime.native_graph.handlers import NativeTaskKindHandlers, UnavailableNativeNodeHandler
from worker.runtime.native_graph.result_maintenance_node import (
    MAINTENANCE_CAPABILITY,
    MAINTENANCE_TASK_KIND,
    NativeResultMaintenanceHandler,
    configured_maintenance_result_store,
)


def build_result_maintenance_handlers(*, default, scope, native_config):
    enabled = native_config.get('result_maintenance')
    if enabled is not None and type(enabled) is not bool:
        raise ValueError('native_result_maintenance_configuration_invalid')
    binding = native_config.get('result_store_binding_id')
    retirement = native_config.get('result_retirement')
    if retirement is not None and type(retirement) is not bool:
        raise ValueError('native_result_retirement_configuration_invalid')
    if retirement is True and (enabled is not True or binding is None
            or RETIREMENT_CAPABILITY not in native_config.get('capabilities', ())):
        raise ValueError('native_result_retirement_deployment_scope_required')
    if binding is not None:
        require_result_store_identifier(binding)
        if enabled is not True:
            raise ValueError('native_result_store_maintenance_scope_required')
    maintenance = UnavailableNativeNodeHandler('native_result_maintenance_disabled')
    if enabled:
        if (MAINTENANCE_TASK_KIND not in native_config.get('allowed_task_types', ())
                or MAINTENANCE_CAPABILITY not in native_config.get('capabilities', ())):
            raise ValueError('native_result_maintenance_deployment_scope_required')
        maintenance = NativeResultMaintenanceHandler(scope=scope, store_binding_id=binding,
            store_factory=partial(configured_maintenance_result_store, binding_id=binding),
            retirement_keys=load_ed25519_verification_key_ring if retirement is True else None)
    return NativeTaskKindHandlers(default=default, handlers={MAINTENANCE_TASK_KIND: maintenance})
