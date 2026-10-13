"""Known delegated execution semantics; registration/grants still own authority."""

from ananta_contracts.native_result_maintenance import MAINTENANCE_CAPABILITY
from ananta_contracts.native_result_retirement import RETIREMENT_CAPABILITY
from ananta_contracts.pi_native import PI_NATIVE_CAPABILITY

NATIVE_DELEGATED_CAPABILITIES = frozenset({
    'retrieval', 'structured_output', 'tool_calling',
    MAINTENANCE_CAPABILITY, RETIREMENT_CAPABILITY, PI_NATIVE_CAPABILITY,
})
