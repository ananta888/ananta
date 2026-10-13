"""Scoped read projections for Harness search, with an explicit field allowlist."""

from __future__ import annotations

from typing import Protocol

from agent.services.harness_benchmark import QUALITY_METRICS, RESOURCE_METRICS


class HarnessOperatorStorePort(Protocol):
    def get(self, *, tenant_id: str, project_id: str, session_id: str) -> dict: ...
    def list(self, *, tenant_id: str, project_id: str, limit: int) -> list[dict]: ...


class HarnessOperatorService:
    def __init__(self, store: HarnessOperatorStorePort):
        self._store = store

    @staticmethod
    def _scope(tenant_id, project_id):
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 200 for value in (tenant_id, project_id)
        ):
            raise ValueError("harness_operator_scope_required")
        return {"tenant_id": tenant_id, "project_id": project_id}

    def list(self, *, tenant_id: str, project_id: str, limit: int = 100) -> dict:
        snapshots = self._store.list(**self._scope(tenant_id, project_id), limit=limit)
        return {"items": [self._projection(item["session_id"], item) for item in snapshots]}

    def get(self, *, tenant_id: str, project_id: str, session_id: str) -> dict:
        snapshot = self._store.get(**self._scope(tenant_id, project_id), session_id=session_id)
        return self._projection(session_id, snapshot)

    def lineage(self, *, tenant_id: str, project_id: str, session_id: str) -> dict:
        projection = self.get(tenant_id=tenant_id, project_id=project_id, session_id=session_id)
        nodes = projection["variants"]
        return {
            "session_id": session_id,
            "revision": projection["revision"],
            "nodes": nodes,
            "edges": [{"parent": parent, "child": node["genome_id"]} for node in nodes for parent in node["parents"]],
        }

    @staticmethod
    def _projection(session_id, snapshot):
        state = snapshot["state"]
        front = set(state.get("pareto_front", ()))
        variants = []
        for item in state["population"]:
            genome = item["genome"]
            evidence = item.get("evidence", {})
            variants.append(
                {
                    "genome_id": genome["genome_id"],
                    "parents": list(genome.get("parents", ())),
                    "is_baseline": genome["genome_id"] == state["baseline"]["genome_id"],
                    "is_search_parent": genome["genome_id"] == state["current_genome"]["genome_id"],
                    "pareto_status": "front" if genome["genome_id"] in front else "dominated" if front else "unranked",
                    "model": {key: genome["model_routing"][key] for key in ("provider_id", "model_id")},
                    "context_budget_tokens": genome["context"]["budget_tokens"],
                    "metrics": {
                        key: value
                        for key, value in item["metrics"].items()
                        if key in QUALITY_METRICS + RESOURCE_METRICS
                    },
                    "evidence": {
                        key: evidence[key] for key in ("run_id", "source_ids", "scope", "synthetic") if key in evidence
                    },
                }
            )
        pending = state.get("pending")
        return {
            "session_id": session_id,
            "revision": snapshot["revision"],
            "phase": state["phase"],
            "reason": state["reason"],
            "suite_id": state["suite_id"],
            "runs": state["runs"],
            "attempts": state["attempts"],
            "charged_cost": state["charged_cost"],
            "charged_tokens": state["charged_tokens"],
            "failed_idea_count": len(state["failed_ideas"]),
            "limits": {
                key: state["policy"][key]
                for key in ("max_runs", "max_seconds", "max_tokens", "max_cost", "max_parallel")
            },
            "active_dispatch_key": pending["dispatch_key"] if pending else None,
            "variants": variants,
            "productive_baseline_modified": False,
        }


def get_harness_operator_service() -> HarnessOperatorService:
    from agent.repositories.harness_search import HarnessSearchStore

    return HarnessOperatorService(HarnessSearchStore())
