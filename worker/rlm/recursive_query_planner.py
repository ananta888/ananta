"""CodeCompass RLM - Recursive Query Planner."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RetrievalStep:
    step_id: str
    query: str
    channel: str
    depth: int
    dependencies: list[str] = field(default_factory=list)
    node_handle: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "query": self.query,
            "channel": self.channel,
            "depth": self.depth,
            "dependencies": list(self.dependencies),
            "node_handle": self.node_handle,
            "extra": dict(self.extra),
        }


@dataclass
class RecursivePlan:
    plan_id: str
    query: str
    steps: list[RetrievalStep]
    max_depth: int
    max_fanout: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "codecompass.rlm-recursive-plan.v1",
            "plan_id": self.plan_id,
            "query": self.query,
            "status": "planned",
            "steps": [step.to_dict() for step in self.steps],
            "budgets": {"max_depth": self.max_depth, "max_fanout": self.max_fanout},
            "metadata": self.metadata,
        }


class RecursiveQueryPlanner:
    def __init__(self, max_depth: int = 3, max_fanout: int = 4, max_steps: int = 24) -> None:
        self.max_depth = max(1, min(int(max_depth), 4))
        self.max_fanout = max(1, min(int(max_fanout), 8))
        self.max_steps = max(1, min(int(max_steps), 64))

    def _generate_plan_id(self, query: str) -> str:
        raw = f"{query}:{datetime.now(timezone.utc).isoformat()}"
        return "rlm-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _create_step(self, query: str, channel: str, depth: int, deps: list[str] | None = None, handle: str = "") -> RetrievalStep:
        step_id = hashlib.sha256(f"{query}|{channel}|{depth}|{handle}".encode()).hexdigest()[:12]
        return RetrievalStep(step_id, query, channel, depth, list(deps or []), handle)

    def create_plan(self, query: str, graph: dict[str, Any] | None = None, root_handles: list[str] | None = None) -> RecursivePlan:
        steps = [self._create_step(query, "exact", 0)]
        handles = list(root_handles or [])[: self.max_fanout]
        for handle in handles:
            if len(steps) >= self.max_fanout + 1:
                break
            steps.append(self._create_step(query, "graph", 1, [steps[0].step_id], handle))
        if graph:
            for node in list((graph or {}).get("nodes") or [])[: self.max_fanout]:
                if len(steps) >= self.max_fanout + 1:
                    break
                title = str(node.get("title") or node.get("id") or "")
                if title and title.lower() not in query.lower():
                    steps.append(self._create_step(f"{query} {title}", "hybrid", 1, [steps[0].step_id], str(node.get("handle") or node.get("id") or "")))
        return RecursivePlan(
            plan_id=self._generate_plan_id(query),
            query=query,
            steps=steps[: self.max_fanout + 1],
            max_depth=self.max_depth,
            max_fanout=self.max_fanout,
            metadata={"created_at": datetime.now(timezone.utc).isoformat()},
        )

    def expand_step(
        self,
        parent: RetrievalStep,
        evidence: list[dict[str, Any]],
    ) -> list[RetrievalStep]:
        if parent.depth >= self.max_depth:
            return []
        children: list[RetrievalStep] = []
        for item in evidence:
            symbol = str(item.get("symbol") or "").strip()
            path = str(item.get("path") or "").strip()
            focus = symbol or path
            if not focus:
                continue
            query = f"{parent.query} {focus}".strip()
            children.append(
                self._create_step(
                    query,
                    "hybrid",
                    parent.depth + 1,
                    [parent.step_id],
                    focus,
                )
            )
            if len(children) >= self.max_fanout:
                break
        return children
