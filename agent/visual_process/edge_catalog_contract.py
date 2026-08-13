"""Neutral bounded contract for preserving canonical VisualProcess edge IDs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agent.services.identity_validation import (
    IdentityValidationError,
    require_canonical_identity,
)

CASEFLOW_EDGE_CATALOG_METADATA_KEY = "caseflow_agent_edge_catalog"
CASEFLOW_EDGE_CATALOG_SCHEMA = "ananta.caseflow_agent_edge_catalog.v1"
MAX_CASEFLOW_EDGE_CATALOG_SIZE = 1024
MAX_CASEFLOW_EDGE_IDENTIFIER_CHARS = 160


@dataclass(frozen=True)
class CanonicalVisualProcessEdge:
    edge_id: str
    source_step_id: str
    target_step_id: str
    edge_kind: Literal["dependency", "back_edge"]

    def to_dict(self) -> dict[str, str]:
        return {
            "edge_id": self.edge_id,
            "source_step_id": self.source_step_id,
            "target_step_id": self.target_step_id,
            "edge_kind": self.edge_kind,
        }


def build_caseflow_edge_catalog(edges: Sequence[Any]) -> dict[str, Any]:
    """Preserve exact edge identity and direction in deterministic order."""

    if len(edges) > MAX_CASEFLOW_EDGE_CATALOG_SIZE:
        raise ValueError("caseflow_edge_catalog_limit_exceeded")
    canonical: list[CanonicalVisualProcessEdge] = []
    seen_ids: set[str] = set()
    for raw in edges:
        edge = edge_from_object(raw)
        if edge.edge_id in seen_ids:
            raise ValueError("caseflow_edge_catalog_duplicate_edge_id")
        seen_ids.add(edge.edge_id)
        canonical.append(edge)
    canonical.sort(
        key=lambda item: (item.source_step_id, item.target_step_id, item.edge_id)
    )
    return {
        "schema": CASEFLOW_EDGE_CATALOG_SCHEMA,
        "complete": True,
        "edges": [edge.to_dict() for edge in canonical],
    }


def build_caseflow_edge_catalog_for_workflow(edges: Sequence[Any]) -> dict[str, Any]:
    """Keep workflow compilation additive when a UI catalog is unavailable."""

    try:
        return build_caseflow_edge_catalog(edges)
    except ValueError as exc:
        reason_code = str(exc)
        if reason_code not in {
            "caseflow_edge_catalog_limit_exceeded",
            "caseflow_edge_catalog_duplicate_edge_id",
            "caseflow_edge_id_invalid",
            "caseflow_source_step_id_invalid",
            "caseflow_target_step_id_invalid",
            "caseflow_edge_kind_invalid",
        }:
            reason_code = "caseflow_edge_catalog_invalid"
        return {
            "schema": CASEFLOW_EDGE_CATALOG_SCHEMA,
            "complete": False,
            "reason_code": reason_code,
            "edge_count": len(edges),
            "edges": [],
        }


def edge_from_object(raw: Any) -> CanonicalVisualProcessEdge:
    def value(name: str) -> Any:
        if isinstance(raw, Mapping):
            return raw.get(name)
        return getattr(raw, name, None)

    return CanonicalVisualProcessEdge(
        edge_id=_required_identity(value("edge_id") or value("id"), "edge_id"),
        source_step_id=_required_identity(
            value("source_step_id") or value("source"),
            "source_step_id",
        ),
        target_step_id=_required_identity(
            value("target_step_id") or value("target"),
            "target_step_id",
        ),
        edge_kind=_edge_kind(raw, value("edge_kind")),
    )


def _edge_kind(raw: Any, explicit: Any) -> Literal["dependency", "back_edge"]:
    if explicit is not None:
        if explicit not in {"dependency", "back_edge"}:
            raise ValueError("caseflow_edge_kind_invalid")
        return explicit
    condition = raw.get("condition") if isinstance(raw, Mapping) else getattr(raw, "condition", None)
    condition_kind = (
        condition.get("kind")
        if isinstance(condition, Mapping)
        else getattr(condition, "kind", None)
    )
    return "back_edge" if condition_kind == "back_edge" else "dependency"


def _required_identity(value: Any, field_name: str) -> str:
    try:
        return require_canonical_identity(
            value,
            field_name=field_name,
            max_length=MAX_CASEFLOW_EDGE_IDENTIFIER_CHARS,
        )
    except IdentityValidationError as exc:
        raise ValueError(f"caseflow_{field_name}_invalid") from exc


__all__ = [
    "CASEFLOW_EDGE_CATALOG_METADATA_KEY",
    "CASEFLOW_EDGE_CATALOG_SCHEMA",
    "MAX_CASEFLOW_EDGE_CATALOG_SIZE",
    "CanonicalVisualProcessEdge",
    "build_caseflow_edge_catalog",
    "build_caseflow_edge_catalog_for_workflow",
    "edge_from_object",
]
