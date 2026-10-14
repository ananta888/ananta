"""Hub-supplied dependencies; model output never grants scheduling authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class ResultReference:
    tool: str
    pointer: str

    def read(self, results: Mapping[str, Mapping[str, Any]]) -> Any:
        value: Any = results[self.tool]
        if self.pointer and not self.pointer.startswith("/"):
            raise ValueError("plan_result_pointer_invalid")
        for part in self.pointer.split("/")[1:]:
            part = part.replace("~1", "/").replace("~0", "~")
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value


@dataclass(frozen=True)
class ResultCondition:
    source: ResultReference
    equals: Any


@dataclass(frozen=True)
class ToolDependency:
    depends_on: tuple[str, ...] = ()
    argument_sources: Mapping[str, ResultReference] = field(default_factory=dict)
    when: tuple[ResultCondition, ...] = ()

    def parents(self) -> frozenset[str]:
        return frozenset(
            (*self.depends_on, *(r.tool for r in self.argument_sources.values()), *(c.source.tool for c in self.when))
        )


def dependency_waves(tools: Sequence[str], dependencies: Mapping[str, ToolDependency]) -> tuple[tuple[str, ...], ...]:
    """Stable topological layers. Cycles, unknown nodes and oversized graphs fail closed."""
    if len(tools) > 32 or len(set(tools)) != len(tools) or set(dependencies) - set(tools):
        raise ValueError("plan_dependency_nodes_invalid")
    remaining = {tool: dependencies.get(tool, ToolDependency()).parents() for tool in tools}
    if any(parents - set(tools) for parents in remaining.values()):
        raise ValueError("plan_dependency_unknown")
    waves = []
    completed: set[str] = set()
    while remaining:
        wave = tuple(tool for tool in tools if tool in remaining and remaining[tool] <= completed)
        if not wave:
            raise ValueError("plan_dependency_cycle")
        waves.append(wave)
        completed.update(wave)
        for tool in wave:
            del remaining[tool]
    return tuple(waves)
