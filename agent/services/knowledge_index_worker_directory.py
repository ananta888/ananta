"""Resolve a bound index worker identity through the Hub registry."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


class RegisteredKnowledgeIndexWorkerDirectory:
    """Map one immutable assignment worker ID to one validated URL."""

    def __init__(self, agent_repository: Any) -> None:
        self._agents = agent_repository
        if not callable(getattr(agent_repository, "get_all", None)):
            raise ValueError("knowledge_index_worker_directory_invalid")

    def resolve_worker_url(self, worker_id: str) -> str:
        normalized_worker_id = str(worker_id or "").strip()
        if not normalized_worker_id:
            raise ValueError("knowledge_index_assignment_worker_invalid")
        matches = [
            agent
            for agent in self._agents.get_all()
            if str(getattr(agent, "name", None) or getattr(agent, "url", ""))
            .strip()
            == normalized_worker_id
        ]
        if len(matches) != 1:
            raise ValueError("knowledge_index_assignment_worker_unavailable")
        agent = matches[0]
        worker_url = str(getattr(agent, "url", "") or "").strip().rstrip("/")
        parsed = urlparse(worker_url)
        if (
            str(getattr(agent, "role", "") or "").strip().lower()
            != "worker"
            or not bool(getattr(agent, "registration_validated", False))
            or str(getattr(agent, "status", "") or "").strip().lower()
            not in {"online", "degraded", "busy"}
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
        ):
            raise ValueError("knowledge_index_assignment_worker_unavailable")
        return worker_url


__all__ = ["RegisteredKnowledgeIndexWorkerDirectory"]
