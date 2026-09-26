"""Pure Hub-side BPMN routing decisions; no task execution or persistence."""

from __future__ import annotations

from agent.services.workflow_runtime.condition_evaluator import DeclarativeConditionEvaluator


def decide_control(control: dict, context: dict) -> dict:
    kind = control["kind"]
    if kind in {"start", "end", "parallel"}:
        return {"control": kind}
    if kind != "exclusive":
        raise ValueError("bpmn_control_kind_unsupported")
    outgoing = control["outgoing"]
    if not outgoing:
        return {"control": kind}
    evaluator = DeclarativeConditionEvaluator()
    default = None
    for edge in outgoing:
        if edge["default"]:
            default = edge
            continue
        decision = evaluator.evaluate(edge["condition"], context)
        if decision.value is None:
            raise ValueError("bpmn_gateway_" + decision.reason_code)
        if decision.matches:
            return {"control": kind, "selected_edge": edge["id"]}
    if default is not None:
        return {"control": kind, "selected_edge": default["id"]}
    raise ValueError("bpmn_gateway_no_matching_flow")


def validate_control_node(node) -> tuple[str, ...]:
    control = node.metadata.get("bpmn_control")
    if node.node_type != "bpmn_control":
        return ("bpmn_control_on_task",) if control is not None else ()
    if not isinstance(control, dict) or set(control) != {"kind", "outgoing"}:
        return ("bpmn_control_contract_invalid",)
    if control["kind"] not in {"start", "end", "parallel", "exclusive", "projection"} or not isinstance(
        control["outgoing"], list
    ):
        return ("bpmn_control_contract_invalid",)
    if node.allowed_tools or node.side_effect_class != "none" or node.input_artifacts or node.output_artifacts:
        return ("bpmn_control_worker_effects_forbidden",)
    if control["kind"] == "projection" and "bpmn_input_projection" not in node.metadata:
        return ("bpmn_projection_control_input_required",)
    if len(control["outgoing"]) > 4096 or control["kind"] != "exclusive" and control["outgoing"]:
        return ("bpmn_control_edge_invalid",)
    ids = set()
    defaults = 0
    for edge in control["outgoing"]:
        if not isinstance(edge, dict) or set(edge) != {"id", "target", "condition", "default"}:
            return ("bpmn_control_edge_invalid",)
        if not isinstance(edge["condition"], dict) or type(edge["default"]) is not bool:
            return ("bpmn_control_edge_invalid",)
        if any(not isinstance(edge[key], str) or not edge[key] for key in ("id", "target")) or edge["id"] in ids:
            return ("bpmn_control_edge_invalid",)
        ids.add(edge["id"])
        defaults += edge["default"]
    if defaults > 1:
        return ("bpmn_control_default_invalid",)
    return ()
