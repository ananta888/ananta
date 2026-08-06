from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "artifacts" / "codecompass_graph_inventory.v1.json"


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _payload(*, complete: bool = True) -> dict[str, object]:
    domain_cursor = None if complete else "eyJmYWNldCI6ImRvbWFpbnMifQ"
    relation_cursor = None if complete else "eyJmYWNldCI6InJlbGF0aW9ucyJ9"
    total_count = 1 if complete else 2
    return {
        "schema": "codecompass_graph_inventory.v1",
        "source_kind": "codecompass_graph",
        "source_ref": "index-example",
        "graph_revision": "sha256:" + "a" * 64,
        "facets": {
            "domains": {
                "items": [
                    {
                        "key": "domain_id:" + "b" * 64,
                        "label": "Orders",
                        "parent_key": None,
                        "depth": 0,
                        "direct_node_count": 2,
                        "subtree_node_count": 3,
                        "has_children": True,
                        "source": "domain_id",
                        "path": "commerce.orders",
                    }
                ],
                "returned": 1,
                "total_count": total_count,
                "complete": complete,
                "next_cursor": domain_cursor,
            },
            "relations": {
                "items": [
                    {
                        "raw_type": "depends_on",
                        "edge_count": 4,
                        "bound_edge_count": 3,
                        "unresolved_edge_count": 1,
                    }
                ],
                "returned": 1,
                "total_count": total_count,
                "complete": complete,
                "next_cursor": relation_cursor,
            },
        },
        "coverage": {
            "graph": {
                "nodes": 4,
                "bound_edges": 3,
                "source_edges": 4,
                "unresolved_edges": 1,
            },
            "domains": {
                "assigned_nodes": 3,
                "unassigned_nodes": 1,
                "returned": 1,
                "total": total_count,
                "complete": complete,
            },
            "relations": {
                "returned": 1,
                "total_count": total_count,
                "complete": complete,
                "edge_count": 4,
                "bound_edge_count": 3,
                "unresolved_edge_count": 1,
            },
            "materialization": {
                "semantic_budget": {
                    "truncated": False,
                    "unresolved_edge_count": 0,
                }
            },
        },
        "diagnostics": {
            "semantic_translation": {
                "status": "ready",
            }
        },
        "warnings": ["1 graph relation was excluded because an endpoint is unavailable."],
        "metadata": {
            "knowledge_index_id": "index-example",
            "view": "inventory",
            "content_graph_revision": "sha256:" + "a" * 64,
            "next_cursor": domain_cursor,
            "relations_next_cursor": relation_cursor,
            "total_nodes": 4,
            "total_edges": 3,
            "total_domains": total_count,
            "total_relation_types": total_count,
        },
        "text_alternative": "Domain inventory for four graph nodes.",
        "artifact_status": {
            "state": "available",
            "reason_code": None,
            "knowledge_index_id": "index-example",
            "manifest_present": True,
        },
    }


@pytest.mark.parametrize("complete", [True, False])
def test_inventory_schema_accepts_complete_and_continuable_payloads(
    complete: bool,
) -> None:
    assert list(_validator().iter_errors(_payload(complete=complete))) == []


def _remove_graph_revision(payload: dict[str, object]) -> None:
    payload.pop("graph_revision")


def _remove_domain_returned(payload: dict[str, object]) -> None:
    payload["facets"]["domains"].pop("returned")  # type: ignore[index]


def _remove_relation_bound_count(payload: dict[str, object]) -> None:
    payload["facets"]["relations"]["items"][0].pop(  # type: ignore[index]
        "bound_edge_count"
    )


def _remove_coverage_source_edges(payload: dict[str, object]) -> None:
    payload["coverage"]["graph"].pop("source_edges")  # type: ignore[index]


def _remove_metadata_relation_cursor(payload: dict[str, object]) -> None:
    payload["metadata"].pop("relations_next_cursor")  # type: ignore[union-attr]


def _remove_metadata_content_revision(payload: dict[str, object]) -> None:
    payload["metadata"].pop("content_graph_revision")  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "mutation",
    [
        _remove_graph_revision,
        _remove_domain_returned,
        _remove_relation_bound_count,
        _remove_coverage_source_edges,
        _remove_metadata_relation_cursor,
        _remove_metadata_content_revision,
    ],
    ids=[
        "graph-revision",
        "domain-returned",
        "relation-bound-count",
        "coverage-source-edges",
        "metadata-relation-cursor",
        "metadata-content-revision",
    ],
)
def test_inventory_schema_rejects_missing_critical_fields(
    mutation: Callable[[dict[str, object]], None],
) -> None:
    payload = _payload()
    mutation(payload)

    assert list(_validator().iter_errors(payload))


@pytest.mark.parametrize(
    ("path", "field"),
    [
        (("coverage", "graph"), "nodes"),
        (("facets", "domains", "items", 0), "depth"),
        (("facets", "relations", "items", 0), "edge_count"),
        (("facets", "relations"), "returned"),
        (("metadata",), "total_relation_types"),
    ],
)
def test_inventory_schema_rejects_negative_counts(
    path: tuple[str | int, ...],
    field: str,
) -> None:
    payload = _payload()
    target: object = payload
    for part in path:
        target = target[part]  # type: ignore[index]
    target[field] = -1  # type: ignore[index]

    assert list(_validator().iter_errors(payload))


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("facets",),
        ("facets", "domains"),
        ("facets", "domains", "items", 0),
        ("facets", "relations", "items", 0),
        ("coverage", "relations"),
        ("metadata",),
        ("artifact_status",),
    ],
)
def test_inventory_schema_rejects_additional_critical_fields(
    path: tuple[str | int, ...],
) -> None:
    payload = _payload()
    target: object = payload
    for part in path:
        target = target[part]  # type: ignore[index]
    target["unexpected"] = True  # type: ignore[index]

    assert list(_validator().iter_errors(payload))


@pytest.mark.parametrize("facet", ["domains", "relations"])
def test_inventory_schema_rejects_inconsistent_completion_cursor(
    facet: str,
) -> None:
    payload = _payload()
    payload["facets"][facet]["next_cursor"] = "unexpected_cursor"  # type: ignore[index]

    assert list(_validator().iter_errors(payload))


def test_inventory_schema_bounds_each_relation_page_to_500_items() -> None:
    payload = _payload()
    item = payload["facets"]["relations"]["items"][0]  # type: ignore[index]
    payload["facets"]["relations"]["items"] = [  # type: ignore[index]
        copy.deepcopy(item) for _ in range(501)
    ]
    payload["facets"]["relations"]["returned"] = 501  # type: ignore[index]

    assert list(_validator().iter_errors(payload))
