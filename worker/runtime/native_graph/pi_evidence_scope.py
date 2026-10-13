"""Task-local signed Pi assignment scope; no issuer, persistence or orchestration."""

import contextvars
import json
from contextlib import contextmanager

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment

_ASSIGNMENT = contextvars.ContextVar('pi_signed_evidence_assignment', default=None)


@contextmanager
def bind_pi_evidence(assignment):
    projection = validate_hub_evidence_assignment(assignment)
    encoded = json.dumps(projection, sort_keys=True, separators=(',', ':'), allow_nan=False)
    token = _ASSIGNMENT.set(encoded)
    try:
        yield
    finally:
        _ASSIGNMENT.reset(token)


def require_pi_evidence(*, task_id):
    encoded = _ASSIGNMENT.get()
    if encoded is None:
        raise ValueError('pi_execution_evidence_scope_required')
    projection = validate_hub_evidence_assignment(json.loads(encoded))
    if projection['task_id'] != task_id:
        raise ValueError('pi_execution_evidence_task_mismatch')
    return projection
