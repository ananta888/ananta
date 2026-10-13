"""BPMN edge lineage from immutable Hub admission, never event/Worker assertions."""

from collections import Counter

from agent.services.bpmn_definition_snapshot import project_bpmn_definition
from agent.visual_process.bpmn_adapter import import_bpmn_xml


def bpmn_edge_provenance(binding):
    metadata = getattr(getattr(binding, 'request', None), 'metadata', {})
    if not metadata.get('bpmn_source_xml'):
        return {}
    snapshot = project_bpmn_definition(binding)
    graph = import_bpmn_xml(snapshot['bpmn_xml']).graph
    output = {}
    for edge in graph.edges:
        origin = edge.metadata.get('bpmn_activation_origin') or {}
        scope = [{key: value for key, value in item.items() if key in {'element_id', 'iteration'}}
                 for item in origin.get('scope', ())]
        value = {'schema': 'ananta.bpmn_edge_provenance.v1',
                 'element_id': origin.get('source_id') or edge.id, 'activation_id': edge.id,
                 'source_step_id': edge.source, 'target_step_id': edge.target,
                 'element_kind': 'sequence_flow' if not origin or origin.get('role') == 'flow' else 'activity_boundary',
                 'scope': scope}
        iterations = [item['iteration'] for item in scope if type(item.get('iteration')) is int]
        if iterations:
            value['iteration'] = iterations[-1]
        output[edge.id] = value
    counts = Counter((value['element_kind'], value['element_id']) for value in output.values())
    for value in output.values():
        value['activation_count'] = counts[value['element_kind'], value['element_id']]
    return output
