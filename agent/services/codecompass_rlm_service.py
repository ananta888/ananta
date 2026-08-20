"""Optional recursive CodeCompass analysis with hard budgets."""

from __future__ import annotations

from typing import Any, Mapping

from agent.services.codecompass_agentic_retrieval_service import (
    get_codecompass_agentic_retrieval_service,
)
from worker.rlm.recursive_query_planner import RecursiveQueryPlanner

RLM_FEATURE_FLAG = "codecompass_rlm_enabled"

_COMPLEXITY_MARKERS = (
    "warum",
    "why",
    "architecture",
    "architecture",
    "zusammenhang",
    "across",
    "end to end",
    "root cause",
    "ursache",
)


def rlm_is_eligible(query: str, *, enabled: bool = False) -> tuple[bool, str]:
    if not enabled:
        return False, "feature_disabled"
    text = str(query or "").strip()
    if len(text.split()) < 6 and not any(token in text.lower() for token in _COMPLEXITY_MARKERS):
        return False, "simple_query_fallback"
    return True, "eligible"


class CodeCompassRlmService:
    def __init__(self, planner: RecursiveQueryPlanner | None = None) -> None:
        self._planner = planner or RecursiveQueryPlanner()

    def analyze(
        self,
        query: str,
        *,
        capability: Mapping[str, Any] | None = None,
        enabled: bool = False,
        architecture_slice: Mapping[str, Any] | None = None,
        max_depth: int = 3,
        max_fanout: int = 4,
        max_steps: int = 24,
    ) -> dict[str, Any]:
        eligible, reason = rlm_is_eligible(query, enabled=enabled)
        if not eligible:
            fallback = get_codecompass_agentic_retrieval_service().retrieve(
                {"schema": "codecompass.agentic-retrieval.v1", "kind": "request", "query": query, "mode": "auto"},
                capability=capability,
            )
            return {
                "schema": "codecompass.rlm-recursive-plan.v1",
                "plan_id": "",
                "query": query,
                "status": "eligible_false",
                "reason": reason,
                "steps": [],
                "merged": fallback,
                "warnings": [reason],
                "budgets": {"max_depth": max_depth, "max_fanout": max_fanout},
                "trace": [],
            }
        planner = RecursiveQueryPlanner(max_depth=max_depth, max_fanout=max_fanout, max_steps=max_steps)
        handles = [str(item.get("handle") or "") for item in list((architecture_slice or {}).get("nodes") or []) if item.get("handle")]
        plan = planner.create_plan(query, graph=dict(architecture_slice or {}), root_handles=handles)
        seen: set[str] = set()
        trace: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        conflicts: list[str] = []
        retrieval = get_codecompass_agentic_retrieval_service()
        queue = list(plan.steps)
        executed_steps = 0
        while queue and executed_steps < planner.max_steps:
            step = queue.pop(0)
            if step.depth > max_depth:
                trace.append({"step_id": step.step_id, "stopped": "depth_budget"})
                continue
            if step.query in seen:
                trace.append({"step_id": step.step_id, "stopped": "cycle_detected"})
                continue
            seen.add(step.query)
            executed_steps += 1
            result = retrieval.retrieve(
                {
                    "schema": "codecompass.agentic-retrieval.v1",
                    "kind": "request",
                    "query": step.query,
                    "mode": "hybrid" if step.channel == "hybrid" else step.channel if step.channel in {"exact", "graph", "vector"} else "auto",
                },
                capability=capability,
            )
            if result.get("status") == "error" and result.get("reason_code") in {"empty_scope", "scope_widening_denied"}:
                return {
                    "schema": "codecompass.rlm-recursive-plan.v1",
                    "plan_id": plan.plan_id,
                    "query": query,
                    "status": "error",
                    "reason": result.get("reason_code"),
                    "steps": plan.to_dict()["steps"],
                    "merged": result,
                    "warnings": [str(result.get("reason_code") or "")],
                    "budgets": plan.to_dict()["budgets"],
                    "trace": trace,
                }
            for item in list(result.get("evidence") or []):
                key = str(item.get("id") or item.get("path") or "")
                existing = next((row for row in evidence if str(row.get("id") or row.get("path")) == key), None)
                if existing and existing.get("excerpt") and item.get("excerpt") and existing.get("excerpt") != item.get("excerpt"):
                    conflicts.append(key)
                    existing.setdefault("conflicts_with", []).append(item.get("excerpt"))
                elif existing is None:
                    evidence.append(dict(item))
            queue.extend(
                child
                for child in planner.expand_step(
                    step,
                    [dict(item) for item in list(result.get("evidence") or []) if isinstance(item, dict)],
                )
                if child.query not in seen
            )
            trace.append({"step_id": step.step_id, "status": result.get("status"), "hits": len(result.get("evidence") or [])})
        if queue:
            trace.append({"stopped": "step_budget", "remaining": len(queue)})
        return {
            "schema": "codecompass.rlm-recursive-plan.v1",
            "plan_id": plan.plan_id,
            "query": query,
            "status": "executed",
            "reason": "ok",
            "steps": plan.to_dict()["steps"],
            "merged": {
                "evidence": evidence,
                "conflicts": conflicts,
                "evidence_conflict": bool(conflicts),
            },
            "warnings": ["evidence_conflict"] if conflicts else [],
            "budgets": {**plan.to_dict()["budgets"], "max_steps": planner.max_steps},
            "trace": trace,
        }


_rlm_service = CodeCompassRlmService()


def get_codecompass_rlm_service() -> CodeCompassRlmService:
    return _rlm_service
