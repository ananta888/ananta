"""Errors and immutable read models for bounded CodeCompass graph reads."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import RLock

from agent.services.codecompass_graph_domain_catalog_service import (
    CodeCompassGraphDomainIndex,
)

UNPREPARED_EDGE_POPULATION = object()


class CodeCompassGraphReadError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class GraphRelationFacet:
    raw_type: str
    edge_count: int
    bound_edge_count: int
    unresolved_edge_count: int

    def to_wire(self) -> dict[str, object]:
        return {
            "raw_type": self.raw_type,
            "edge_count": self.edge_count,
            "bound_edge_count": self.bound_edge_count,
            "unresolved_edge_count": self.unresolved_edge_count,
        }


@dataclass
class GraphDerivedSnapshot:
    domain_index: CodeCompassGraphDomainIndex
    nodes: tuple[Mapping[str, object], ...]
    edges: tuple[Mapping[str, object], ...]
    node_ids: frozenset[str]
    bound_edges: tuple[Mapping[str, object], ...]
    unresolved_edges: tuple[Mapping[str, object], ...]
    edge_indices_by_endpoint: Mapping[str, tuple[int, ...]]
    relation_facets: tuple[GraphRelationFacet, ...]
    prepared_edge_population: object = field(
        default=UNPREPARED_EDGE_POPULATION,
        repr=False,
    )
    prepared_edge_population_lock: RLock = field(
        default_factory=RLock,
        repr=False,
    )


@dataclass(frozen=True)
class GraphRevisionIdentity:
    content_revision: str
    evidence_revision: str


@dataclass(frozen=True)
class GraphPayloadRevision:
    payload: Mapping[str, object]
    identity: GraphRevisionIdentity


@dataclass(frozen=True)
class GraphInventoryCursor:
    facet: str | None
    offset: int
