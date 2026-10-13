"""Read-only model toolbox over assigned owner ports, never legacy assist."""

from copy import deepcopy

from ananta_contracts.meet_companion_model_input import render_tool_result
from worker.meet_media.llm_tools import (
    DEFINITION,
    HUB_TOOL_SPECS,
    MAX_CALLS_PER_REPLY,
    NAME,
    HubToolSpec,
    ToolBox,
    parse_arguments,
)


class AssignedToolResults:
    def __init__(self):
        self._responses = []

    def reset(self):
        self._responses.clear()

    def append(self, response):
        if len(self._responses) >= 16:
            raise ValueError("meet_companion_assigned_tool_budget_exhausted")
        self._responses.append(deepcopy(response))

    def snapshot(self):
        return deepcopy(self._responses)


class AssignedReadTool:
    def __init__(self, spec, ports, results):
        self.spec, self.ports, self.results = spec, ports, results
        self.name, self.definition = spec.name, deepcopy(spec.definition)
        self.calls, self.sources = [], []

    def reset(self):
        self.calls.clear()
        self.sources.clear()
        self.results.reset()

    def run(self, raw):
        arguments = {}
        for field, value in parse_arguments(raw).items():
            converter = self.spec.fields.get(field)
            converted = converter(value) if converter is not None else None
            if converted is not None:
                arguments[field] = converted
        if any(field not in arguments for field in self.spec.required):
            raise ValueError("meet_companion_assigned_tool_arguments_invalid")
        if len(self.calls) >= MAX_CALLS_PER_REPLY:
            raise ValueError("meet_companion_assigned_tool_budget_exhausted")
        response = self.ports.call("tool", {"tool": self.spec.mcp_name, "arguments": arguments})
        if not isinstance(response, dict) or response.get("tool") != self.spec.mcp_name:
            raise ValueError("meet_companion_assigned_tool_result_invalid")
        text = render_tool_result(response)
        self.results.append(response)
        self.sources.extend(deepcopy(response["sources"]))
        self.calls.append({"tool": self.name, "query": str(arguments.get("query") or "")[:200],
                           "snippets": len(response["sources"]), "failed": False})
        return text


def assigned_toolbox(ports):
    properties = DEFINITION["function"]["parameters"]["properties"]
    search = HubToolSpec(NAME, "codecompass.retrieve", DEFINITION["function"]["description"], {
        "query": (properties["query"], lambda value: value.strip()[:200] if isinstance(value, str) else None),
        "limit": (properties["limit"], lambda value: max(1, min(8, value)) if type(value) is int else None),
    }, required=("query",), usage="codecompass_search braucht query")
    results = AssignedToolResults()
    box = ToolBox([AssignedReadTool(spec, ports, results) for spec in (search, *HUB_TOOL_SPECS)])
    return box, results
