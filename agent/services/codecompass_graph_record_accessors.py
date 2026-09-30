"""Tolerant field accessors and warning texts for CodeCompass graph payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def graph_diagnostics(raw: Mapping[str, object]) -> dict[str, object]:
    value = raw.get("diagnostics")
    return dict(value) if isinstance(value, Mapping) else {}


def graph_semantic_translation(
    diagnostics: Mapping[str, object],
) -> Mapping[str, object]:
    value = diagnostics.get("semantic_translation")
    return value if isinstance(value, Mapping) else {}


def graph_semantic_budget(
    semantic_translation: Mapping[str, object],
) -> dict[str, object]:
    value = semantic_translation.get("semantic_budget")
    return dict(value) if isinstance(value, Mapping) else {}


def graph_mappings(values: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(value for value in values if isinstance(value, Mapping))


def graph_node_id(node: Mapping[str, object]) -> str:
    return str(node.get("id") or node.get("node_id") or "").strip()


def graph_edge_endpoints(edge: Mapping[str, object]) -> tuple[str, str]:
    return (
        str(edge.get("source_id") or edge.get("source") or edge.get("from") or "").strip(),
        str(edge.get("target_id") or edge.get("target") or edge.get("to") or "").strip(),
    )


def graph_edge_relation(edge: Mapping[str, object]) -> str:
    attributes = edge.get("attributes")
    nested = attributes if isinstance(attributes, Mapping) else {}
    return str(
        edge.get("raw_edge_type")
        or nested.get("raw_edge_type")
        or edge.get("edge_type")
        or edge.get("relation")
        or edge.get("type")
        or "related"
    )


def graph_semantic_warnings(
    *,
    semantic_budget: Mapping[str, object],
    semantic_translation: Mapping[str, object],
) -> list[str]:
    warnings: list[str] = []
    if bool(semantic_budget.get("truncated")):
        warnings.append(
            "The semantic graph reached its configured record budget; the topology is a documented partial view."
        )
    semantic_unresolved = int(semantic_budget.get("unresolved_edge_count") or 0)
    if semantic_unresolved:
        warnings.append(
            f"{semantic_unresolved} semantic graph relation"
            f"{'s were' if semantic_unresolved != 1 else ' was'} not materialized "
            "because no source-grounded endpoint was available."
        )
    if str(semantic_translation.get("status") or "").lower() == "degraded" and not warnings:
        warnings.append("The semantic graph reports degraded materialization.")
    return warnings


def unresolved_graph_warning(count: int) -> str:
    return (
        f"{count} graph relation"
        f"{'s have' if count != 1 else ' has'} an unavailable source or target "
        "node. The staged edge stream retains these relations; reindex the "
        "source to materialize current endpoints."
    )
