"""Closed Hub BPMN edge observations; selection is not task execution evidence."""

from collections.abc import Mapping

BPMN_EDGE_STATES = frozenset({'pending', 'selected', 'skipped', 'completed', 'failed', 'blocked', 'cancelled'})


def valid_bpmn_edge_observation(raw, *, tenant_id, workflow_id, run_id, plan_hash, definition_hash, directions):
    if not isinstance(raw, Mapping):
        return False
    event_type = raw.get('event_type')
    if (not isinstance(event_type, str)
            or event_type not in {'workflow.bpmn.edge.' + value for value in BPMN_EDGE_STATES}):
        return False
    payload = raw.get('payload')
    if not isinstance(payload, Mapping):
        return False
    edge_id = payload.get('edge_id')
    if not isinstance(edge_id, str):
        return False
    edge = directions.get(edge_id)
    return bool(raw.get('schema') == 'ananta.workflow_event.v1' and raw.get('actor') == 'hub'
        and (raw.get('tenant_id'), raw.get('workflow_id'), raw.get('run_id')) == (tenant_id, workflow_id, run_id)
        and (payload.get('plan_hash'), payload.get('definition_hash')) == (plan_hash, definition_hash)
        and edge is not None
        and (payload.get('source_step_id'), payload.get('target_step_id')) == edge
        and raw.get('step_id') == edge[1]
        and payload.get('edge_status') == event_type.removeprefix('workflow.bpmn.edge.')
        and all(type(payload.get(key)) is int and payload[key] >= 0 for key in ('source_attempt', 'target_attempt')))
