"""Immutable private-result identity shared by Hub admission and Worker storage."""

import hashlib
import json

from ananta_contracts.hub_evidence import validate_hub_evidence_assignment
from ananta_contracts.native_task_execution import native_execution_request


def native_result_binding(task, evidence_assignment=None):
    projected = native_execution_request(task)['task']
    bound = projected
    if evidence_assignment is not None:
        evidence = validate_hub_evidence_assignment(evidence_assignment)
        if projected['task_kind'] != 'pi_coding_agent' or evidence['task_id'] != projected['id']:
            raise ValueError('native_result_store_evidence_binding_denied')
        bound = {'task': projected, 'evidence_assignment': evidence}
    encoded = json.dumps(bound, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    return projected, hashlib.sha256(encoded.encode()).hexdigest()
