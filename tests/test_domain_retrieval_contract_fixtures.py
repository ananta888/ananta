from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.services.domain_retrieval_service import DomainRetrievalService

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "domain_retrieval_contract" / "cases.json"


class _FixtureLoader:
    def __init__(self, profiles: list[dict[str, Any]]) -> None:
        self._profiles = [dict(profile) for profile in profiles]

    def profiles_for_retrieval(
        self,
        domain_id: str,
        *,
        retrieval_intent: str,
        max_profiles: int = 8,
    ) -> list[dict[str, Any]]:
        del domain_id, retrieval_intent, max_profiles
        return [dict(profile) for profile in self._profiles]


class _FixtureBackend:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = dict(payload)
        self.calls = 0
        self.last_source_types: list[str] = []

    def retrieve_context(
        self,
        query: str,
        *,
        task_kind: str | None = None,
        retrieval_intent: str | None = None,
        task_id: str | None = None,
        goal_id: str | None = None,
        neighbor_task_ids: list[str] | None = None,
        source_types: list[str] | None = None,
    ) -> dict[str, object]:
        del query, task_kind, retrieval_intent, task_id, goal_id, neighbor_task_ids
        self.calls += 1
        self.last_source_types = list(source_types or [])
        return dict(self._payload)


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [dict(item) for item in list(payload.get("cases") or []) if isinstance(item, dict)]


def test_domain_retrieval_contract_fixtures_route_source_types_and_bounds() -> None:
    for case in _load_cases():
        loader = _FixtureLoader(profiles=list(case.get("profiles") or []))
        backend = _FixtureBackend(payload=dict(case.get("backend_payload") or {}))
        service = DomainRetrievalService(
            rag_profile_loader=loader,  # type: ignore[arg-type]
            retrieval_backend=backend,  # type: ignore[arg-type]
            max_results_default=3,
            max_results_limit=5,
        )

        result = service.retrieve(
            domain_id=str(case.get("domain_id") or ""),
            retrieval_intent=str(case.get("retrieval_intent") or ""),
            query=str(case.get("query") or ""),
            max_results=int(case.get("max_results") or 3),
            context_summary={"fixture_id": case["id"]},
        )

        expectations = dict(case.get("expectations") or {})
        assert result["status"] == "ok", case["id"]
        assert backend.calls == 1, case["id"]
        assert backend.last_source_types == list(expectations.get("source_types") or []), case["id"]
        assert [chunk["source_id"] for chunk in list(result.get("chunks") or [])] == list(
            expectations.get("chunk_source_ids") or []
        ), case["id"]
        assert len(result["chunks"]) <= int(case.get("max_results") or 3), case["id"]
