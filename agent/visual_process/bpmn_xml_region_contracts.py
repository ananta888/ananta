"""Closed source contracts for bounded XML regions; no runtime or persistence.

Ananta XML metadata declares ``bpmn_region`` with schema
``ananta.bpmn_region.v1`` and ``input_mapping`` / ``output_mapping``. A loop
declares ``bpmn_loop`` with schema ``ananta.bpmn_loop.v1`` and
``input_mapping`` / ``repeat_mapping``; both maps name the same state fields.
Each mapping is an alias-to-literal-path dictionary rooted in input/results.

Every task and gateway in a document containing regions declares its own
``bpmn_input_projection``. Lexical ``input`` names the region entry (or current
iteration state); ``results`` names only siblings' exported results. Empty
maps mean empty data. The runtime never substitutes ambient workflow data.

Generated region entries/exits are Hub projection controls. A loop exit adds
``bpmn_projection_join`` (schema ``ananta.bpmn_projection_join.v1``, sources)
whose only valid result is the object exported by exactly one completed
incoming return node. Source identities below are compile lineage, not Hub
Evidence Registry SRC_/RUN_ identities or authority grants.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from copy import deepcopy

from agent.services.bpmn_input_projection import PROJECTION_KEY, PROJECTION_SCHEMA, validate_input_projection
from agent.services.bpmn_projection_control import JOIN_KEY
from agent.visual_process.bpmn_execution_support import (
    ANANTA_NS,
    BPMN_NS,
    BpmnExecutionError,
    BpmnExecutionIssue,
    parse_ananta_metadata,
)

ORIGIN_KEY = "bpmn_activation_origin"
REGIONS_KEY = "bpmn_xml_regions"
CONTROL_KEY = "bpmn_region_control"
BOUND_METADATA = (PROJECTION_KEY, ORIGIN_KEY, CONTROL_KEY, JOIN_KEY)


def reject(owner, reason, detail=""):
    raise BpmnExecutionError([BpmnExecutionIssue(reason, owner, detail)])


def tag(local):
    return f"{{{BPMN_NS}}}{local}"


def metadata(element):
    value = element.find(f"{tag('extensionElements')}/{{{ANANTA_NS}}}metadata")
    return parse_ananta_metadata(value.text or "{}") if value is not None else {}


def set_metadata(element, value):
    for child in element.findall(tag("extensionElements")):
        element.remove(child)
    extension = ET.SubElement(element, tag("extensionElements"))
    ET.SubElement(extension, f"{{{ANANTA_NS}}}metadata").text = json.dumps(value, sort_keys=True, allow_nan=False)


def projection(inputs=None, results=None):
    return {"schema": PROJECTION_SCHEMA, "workflow_input": inputs or {}, "dependency_results": results or {}}


def validate_projection(value, owner):
    issues = validate_input_projection(value)
    if issues:
        reject(owner, "bpmn_region_projection_invalid", str(issues))


def mapping(value, owner):
    """Mappings share the pure runtime port's literal dictionary-path grammar."""
    candidate = projection(value)
    if not isinstance(value, dict):
        reject(owner, "bpmn_region_mapping_required")
    validate_projection(candidate, owner)
    return deepcopy(value)


def rebind_mapping(value, *, owner, inputs, results):
    """Resolve lexical input/results names to canonical DAG node identities."""
    bound = mapping(value, owner)
    for alias, path in bound.items():
        if path[0] == "input":
            if inputs is not None:
                entry, fields = inputs
                if path[1] not in fields:
                    reject(owner, "bpmn_region_input_unknown", path[1])
                bound[alias] = ["results", entry, *path[1:]]
        elif path[1] not in results:
            reject(owner, "bpmn_region_result_unknown", path[1])
        else:
            bound[alias] = ["results", results[path[1]], *path[2:]]
    # Prefixing a scoped source can increase path depth by one.
    validate_projection(projection(bound), owner)
    return bound


def rebind_projection(value, *, owner, inputs, results):
    validate_projection(value, owner)
    bound = {"schema": PROJECTION_SCHEMA}
    for field in ("workflow_input", "dependency_results"):
        bound[field] = rebind_mapping(value[field], owner=owner, inputs=inputs, results=results)
    validate_projection(bound, owner)
    return bound
