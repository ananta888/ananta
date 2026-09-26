"""Lower bounded structured XML regions into the existing canonical DAG.

This is an import transformation, not an execution engine. Source admission
repeats it before request/plan adaptation. The component activation compiler
remains a blocked preview API and cannot authorize these XML definitions.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import asdict, dataclass

from agent.services.bpmn_input_projection import PROJECTION_KEY
from agent.visual_process.bpmn_activation_contracts import ActivationLimits
from agent.visual_process.bpmn_conditions import compile_condition
from agent.visual_process.bpmn_xml_region_contracts import (
    CONTROL_KEY,
    JOIN_KEY,
    ORIGIN_KEY,
    mapping,
    metadata,
    projection,
    rebind_mapping,
    rebind_projection,
    reject,
    set_metadata,
    tag,
)

_NODES = {
    "startEvent",
    "endEvent",
    "task",
    "serviceTask",
    "userTask",
    "exclusiveGateway",
    "parallelGateway",
    "intermediateCatchEvent",
}
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")


@dataclass(frozen=True)
class _Fragment:
    entry: str
    exit: str


def has_xml_regions(root):
    return any(element.tag in {tag("subProcess"), tag("standardLoopCharacteristics")} for element in root.iter())


def expand_xml_regions(root, *, source_sha256, limits=ActivationLimits()):
    """Return a detached flattened XML tree plus its source-bound manifest."""
    return _Expansion(root, source_sha256, limits).expand()


class _Expansion:
    def __init__(self, root, source_sha256, limits):
        self.root, self.digest, self.limits = root, source_sha256, limits
        self.nodes, self.flows = [], []
        self.identities = set()

    def expand(self):
        process = self.root.find(tag("process"))
        for element in process.iter():
            if element.tag in {tag(name) for name in _NODES | {"subProcess", "sequenceFlow"}}:
                owner = element.get("id", "")
                if not _ID.fullmatch(owner):
                    reject(owner, "bpmn_execution_id_invalid")
                value = metadata(element)
                if any(key in value for key in (ORIGIN_KEY, CONTROL_KEY, JOIN_KEY, "bpmn_control")):
                    reject(owner, "bpmn_region_metadata_reserved")
        self._scope(process, (), None)
        expanded = deepcopy(self.root)
        target = expanded.find(tag("process"))
        for child in list(target):
            if child.tag not in {tag("extensionElements"), tag("documentation")}:
                target.remove(child)
        target.extend(self.nodes)
        target.extend(self.flows)
        return expanded, {
            "schema": "ananta.bpmn_xml_regions.v1",
            "source_sha256": self.digest,
            "limits": asdict(self.limits),
        }

    def _id(self, source, path, role="node"):
        if not path and role == "node":
            return source
        payload = json.dumps([self.digest, source, path, role], sort_keys=True, separators=(",", ":"))
        return "bpr_" + hashlib.sha256(payload.encode()).hexdigest()

    def _origin(self, source, path, role):
        return {
            "schema": "ananta.bpmn_xml_origin.v1",
            "source_sha256": self.digest,
            "source_id": source,
            "scope": deepcopy(list(path)),
            "role": role,
        }

    def _emit(self, element, *, flow=False):
        owner = element.get("id")
        if owner in self.identities:
            reject(owner, "bpmn_region_identity_collision")
        self.identities.add(owner)
        values, maximum = (self.flows, self.limits.max_edges) if flow else (self.nodes, self.limits.max_nodes)
        if len(values) >= maximum:
            reject(owner, "bpmn_activation_edge_budget_exceeded" if flow else "bpmn_activation_node_budget_exceeded")
        values.append(element)

    def _flow(self, source, target, owner, path, role, expression=None):
        identity = self._id(owner, path, role)
        flow = ET.Element(tag("sequenceFlow"), id=identity, sourceRef=source, targetRef=target)
        if expression is not None:
            ET.SubElement(flow, tag("conditionExpression"), language="ananta-condition-v1").text = expression
        set_metadata(flow, {ORIGIN_KEY: self._origin(owner, path, role)})
        self._emit(flow, flow=True)
        return identity

    def _control(self, identity, owner, path, role, view=None, *, join=None, decision=False):
        node = ET.Element(tag("exclusiveGateway"), id=identity, name=f"{owner}: {role}")
        values = {
            ORIGIN_KEY: self._origin(owner, path, role),
            PROJECTION_KEY: view if view is not None else projection(),
        }
        if not decision:
            values[CONTROL_KEY] = "projection"
        if join is not None:
            values[JOIN_KEY] = {"schema": "ananta.bpmn_projection_join.v1", "sources": join}
        set_metadata(node, values)
        self._emit(node)

    def _scope(self, container, path, inputs):
        if len(path) > self.limits.max_depth:
            reject(container.get("id", ""), "bpmn_activation_depth_exceeded")
        children = [child for child in container if child.tag in {tag(name) for name in _NODES | {"subProcess"}}]
        flows = container.findall(tag("sequenceFlow"))
        self._topology(container, children, flows, scoped=bool(path))
        results = {child.get("id"): self._id(child.get("id"), path) for child in children}
        fragments = {child.get("id"): self._element(child, path, inputs, results) for child in children}
        for flow in flows:
            source, target = flow.get("sourceRef"), flow.get("targetRef")
            copied = deepcopy(flow)
            copied.set("id", self._id(flow.get("id"), path))
            copied.set("sourceRef", fragments[source].exit)
            copied.set("targetRef", fragments[target].entry)
            set_metadata(copied, {**metadata(flow), ORIGIN_KEY: self._origin(flow.get("id"), path, "flow")})
            self._emit(copied, flow=True)
        return fragments, results

    def _element(self, element, path, inputs, results, *, ignore_loop=False):
        if not ignore_loop and element.find(tag("standardLoopCharacteristics")) is not None:
            return self._loop(element, path, inputs, results)
        if element.tag == tag("subProcess"):
            return self._subprocess(element, path, inputs, results)
        owner = element.get("id")
        identity = self._id(owner, path)
        copied = deepcopy(element)
        copied.set("id", identity)
        for name in ("incoming", "outgoing", "standardLoopCharacteristics"):
            for child in copied.findall(tag(name)):
                copied.remove(child)
        if copied.get("default"):
            copied.set("default", self._id(copied.get("default"), path))
        values = metadata(element)
        if path:
            self._no_artifacts(values, owner)
        view = values.get(PROJECTION_KEY)
        if element.tag in {tag("startEvent"), tag("endEvent")}:
            if path:
                copied.tag = tag("exclusiveGateway")
                values["kind"] = "decision"
                values[CONTROL_KEY] = "projection"
            view = view if view is not None else projection()
        # Outer consumers also use lexical projections; otherwise the legacy
        # ambient snapshot would leak a subprocess's private child results.
        if view is None:
            reject(owner, "bpmn_region_child_projection_required")
        values[PROJECTION_KEY] = rebind_projection(view, owner=owner, inputs=inputs, results=results)
        for key in ("bpmn_loop", "bpmn_region"):
            values.pop(key, None)
        values[ORIGIN_KEY] = self._origin(owner, path, "body" if ignore_loop else "node")
        set_metadata(copied, values)
        self._emit(copied)
        return _Fragment(identity, identity)

    def _subprocess(self, element, path, inputs, results):
        owner = element.get("id")
        values = metadata(element)
        self._no_artifacts(values, owner)
        if values.get("allowed_tools") or values.get("gate") or values.get("side_effect_class", "none") != "none":
            reject(owner, "bpmn_region_wrapper_effects_unsupported")
        spec = values.get("bpmn_region")
        if (
            not isinstance(spec, dict)
            or set(spec) != {"schema", "input_mapping", "output_mapping"}
            or spec["schema"] != "ananta.bpmn_region.v1"
        ):
            reject(owner, "bpmn_region_mapping_required")
        input_map, output_map = mapping(spec["input_mapping"], owner), mapping(spec["output_mapping"], owner)
        entry = self._id(owner, path, "entry")
        exit_id = self._id(owner, path)
        self._control(
            entry,
            owner,
            path,
            "entry",
            projection(rebind_mapping(input_map, owner=owner, inputs=inputs, results=results)),
        )
        inner_path = (*path, {"element_id": owner})
        fragments, inner_results = self._scope(element, inner_path, (entry, set(input_map)))
        start = next(child.get("id") for child in element if child.tag == tag("startEvent"))
        end = next(child.get("id") for child in element if child.tag == tag("endEvent"))
        self._flow(entry, fragments[start].entry, owner, path, "enter-flow")
        self._control(
            exit_id,
            owner,
            path,
            "exit",
            projection(rebind_mapping(output_map, owner=owner, inputs=(entry, set(input_map)), results=inner_results)),
        )
        self._flow(fragments[end].exit, exit_id, owner, path, "leave-flow")
        return _Fragment(entry, exit_id)

    def _loop(self, element, path, inputs, results):
        owner = element.get("id")
        if element.tag not in {tag(name) for name in ("task", "serviceTask", "userTask", "subProcess")}:
            reject(owner, "bpmn_loop_activity_required")
        loop = element.find(tag("standardLoopCharacteristics"))
        if loop.get("testBefore") not in {"true", "1"}:
            reject(owner, "bpmn_loop_test_before_required")
        maximum = loop.get("loopMaximum", "")
        if not re.fullmatch(r"[1-9][0-9]{0,3}", maximum) or int(maximum) > self.limits.max_iterations:
            reject(owner, "bpmn_loop_finite_maximum_required")
        condition = loop.find(tag("loopCondition"))
        expression = (condition.text or "").strip() if condition is not None else ""
        predicate = compile_condition(expression, element_id=owner)
        spec = metadata(element).get("bpmn_loop")
        if (
            not isinstance(spec, dict)
            or set(spec) != {"schema", "input_mapping", "repeat_mapping"}
            or spec["schema"] != "ananta.bpmn_loop.v1"
        ):
            reject(owner, "bpmn_loop_mapping_required")
        initial, repeat = mapping(spec["input_mapping"], owner), mapping(spec["repeat_mapping"], owner)
        if set(initial) != set(repeat):
            reject(owner, "bpmn_loop_state_fields_mismatch")
        pending = [predicate]
        while pending:
            item = pending.pop()
            pending.extend(item.get("conditions", []))
            if "condition" in item:
                pending.append(item["condition"])
            if "field" in item:
                parts = item["field"].split(".")
                if parts[0] != "input" or parts[1] not in initial:
                    reject(owner, "bpmn_loop_condition_scope_invalid")
        maximum = int(maximum)
        states = [self._id(owner, path, f"state-{index}") for index in range(maximum + 1)]
        self._control(
            states[0],
            owner,
            path,
            "loop-entry",
            projection(rebind_mapping(initial, owner=owner, inputs=inputs, results=results)),
        )
        returns = []
        for index in range(maximum + 1):
            iteration = (*path, {"element_id": owner, "iteration": index})
            if len(iteration) > self.limits.max_depth:
                reject(owner, "bpmn_activation_depth_exceeded")
            view = projection({alias: ["results", states[index], alias] for alias in initial})
            returned = self._id(owner, iteration, "return")
            self._control(returned, owner, iteration, "loop-return", view)
            returns.append(returned)
            if index == maximum:
                self._flow(states[index], returned, owner, iteration, "maximum-flow")
                continue
            check = self._id(owner, iteration, "check")
            self._control(check, owner, iteration, "loop-check", view, decision=True)
            self._flow(states[index], check, owner, iteration, "check-flow")
            self._flow(check, returned, owner, iteration, "exit-flow", f"not ({expression})")
            body = self._element(element, iteration, (states[index], set(initial)), {}, ignore_loop=True)
            self._flow(check, body.entry, owner, iteration, "repeat-flow", expression)
            rebound = rebind_mapping(
                repeat, owner=owner, inputs=(states[index], set(initial)), results={owner: body.exit}
            )
            self._control(states[index + 1], owner, iteration, "loop-state", projection(rebound))
            self._flow(body.exit, states[index + 1], owner, iteration, "state-flow")
        exit_id = self._id(owner, path)
        self._control(exit_id, owner, path, "loop-exit", join=returns)
        for index, returned in enumerate(returns):
            self._flow(returned, exit_id, owner, path, f"join-{index}")
        return _Fragment(states[0], exit_id)

    @staticmethod
    def _no_artifacts(values, owner):
        if values.get("policy_scope") or values.get("policyScope"):
            reject(owner, "bpmn_region_policy_override_unsupported", "Children inherit the Hub request policy scope.")
        if (
            values.get("inputs")
            or values.get("outputs")
            or any(values.get("io", {}).get(key) for key in ("inputs", "outputs"))
        ):
            reject(owner, "bpmn_region_artifact_mapping_unsupported")

    @staticmethod
    def _topology(container, children, flows, *, scoped):
        nodes = {child.get("id"): child for child in children}
        incoming, outgoing = {key: set() for key in nodes}, {key: set() for key in nodes}
        for flow in flows:
            source, target = flow.get("sourceRef"), flow.get("targetRef")
            if source not in nodes or target not in nodes:
                reject(flow.get("id", ""), "bpmn_flow_reference_invalid")
            incoming[target].add(source)
            outgoing[source].add(target)
        degree = {key: len(value) for key, value in incoming.items()}
        pending = [key for key in nodes if not degree[key]]
        visited = 0
        while pending:
            key = pending.pop()
            visited += 1
            for target in outgoing[key]:
                degree[target] -= 1
                if not degree[target]:
                    pending.append(target)
        if visited != len(nodes):
            reject(
                container.get("id", ""), "bpmn_loop_unsupported", "Arbitrary sequence-flow cycles remain unsupported."
            )
        if scoped:
            starts = [key for key, child in nodes.items() if child.tag == tag("startEvent")]
            ends = [key for key, child in nodes.items() if child.tag == tag("endEvent")]
            if (
                len(starts) != 1
                or len(ends) != 1
                or {key for key in nodes if not incoming[key]} != set(starts)
                or {key for key in nodes if not outgoing[key]} != set(ends)
            ):
                reject(container.get("id", ""), "bpmn_region_single_entry_exit_required")
