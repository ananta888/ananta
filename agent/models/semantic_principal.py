"""Authenticated principal of Hub-owned semantic compute contracts.

A frozen value type shared by routes, services and the contract repository.
It lives in the model layer so routes do not import ``agent.repositories``;
``agent.repositories.semantic_contract_repository`` re-exports it unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SemanticPrincipal:
    tenant_id: str
    subject: str


__all__ = ["SemanticPrincipal"]
