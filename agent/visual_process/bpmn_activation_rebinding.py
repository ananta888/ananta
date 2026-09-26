"""Rebind the closed condition DSL without textual replacement or evaluation."""

from __future__ import annotations

import math
import re

from agent.visual_process.bpmn_activation_contracts import BpmnActivationError

_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z_][A-Za-z0-9_-]*)+$")


def _literal(value, element_id):
    if (
        type(value) not in {type(None), bool, int, float, str}
        or type(value) is float
        and not math.isfinite(value)
        or isinstance(value, str)
        and len(value) > 4096
    ):
        raise BpmnActivationError("bpmn_activation_condition_literal_invalid", element_id)
    return value


def rebind_condition(condition: dict, nodes: dict[str, str], edges: dict[str, str], *, element_id: str) -> dict:
    remaining = [256]

    def visit(value, depth):
        remaining[0] -= 1
        if depth > 16 or remaining[0] < 0:
            raise BpmnActivationError("bpmn_activation_condition_limit", element_id)
        if not isinstance(value, dict):
            raise BpmnActivationError("bpmn_activation_condition_invalid", element_id)
        # Traverse structural children under the depth/node limit before copying
        # leaf values; deepcopy of the whole tree would defeat that bound.
        result = dict(value)
        op = result.get("op")
        if op in {"all", "any"}:
            children = result.get("conditions")
            if (
                set(result) != {"op", "conditions"}
                or not isinstance(children, list)
                or not children
                or len(children) > 256
            ):
                raise BpmnActivationError("bpmn_activation_condition_invalid", element_id)
            result["conditions"] = [visit(child, depth + 1) for child in children]
        elif op == "not":
            if set(result) != {"op", "condition"}:
                raise BpmnActivationError("bpmn_activation_condition_invalid", element_id)
            result["condition"] = visit(result["condition"], depth + 1)
        elif op == "always":
            if set(result) != {"op"}:
                raise BpmnActivationError("bpmn_activation_condition_invalid", element_id)
        elif op in {"eq", "ne", "in", "exists", "gt", "ge", "lt", "le"}:
            expected = {"op", "field"} if op == "exists" else {"op", "field", "value"}
            if (
                set(result) != expected
                or not isinstance(result.get("field"), str)
                or not _FIELD.fullmatch(result["field"])
            ):
                raise BpmnActivationError("bpmn_activation_condition_invalid", element_id)
            if op == "in":
                if not isinstance(result["value"], list) or len(result["value"]) > 128:
                    raise BpmnActivationError("bpmn_activation_condition_literal_invalid", element_id)
                result["value"] = [_literal(item, element_id) for item in result["value"]]
            elif op != "exists":
                result["value"] = _literal(result["value"], element_id)
            parts = result["field"].split(".")
            if parts[0] == "results" and len(parts) >= 3:
                if parts[1] not in nodes:
                    raise BpmnActivationError("bpmn_activation_result_scope_unsupported", element_id, parts[1])
                parts[1] = nodes[parts[1]]
                result["field"] = ".".join(parts)
                if parts[2:] == ["selected_edge"] and op != "exists":
                    values = result["value"] if op == "in" else [result["value"]]
                    if not isinstance(values, list) or any(
                        not isinstance(item, str) or item not in edges for item in values
                    ):
                        raise BpmnActivationError("bpmn_activation_edge_reference_unknown", element_id)
                    rebound = [edges[item] for item in values]
                    result["value"] = rebound if op == "in" else rebound[0]
            elif parts[0] != "input" or len(parts) < 2:
                raise BpmnActivationError("bpmn_activation_condition_scope_unsupported", element_id)
        else:
            raise BpmnActivationError("bpmn_activation_condition_invalid", element_id)
        return result

    return visit(condition, 0)
