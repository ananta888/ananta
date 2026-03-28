from __future__ import annotations


def build_embedding_records(index_records: list[dict]) -> list[dict]:
    embedding_records: list[dict] = []
    for record in index_records:
        embedding_records.append({
            "id": record.get("id"),
            "kind": record.get("kind"),
            "file": record.get("file"),
            "embedding_text": record.get("embedding_text", ""),
            "summary": record.get("summary"),
            "role_labels": record.get("role_labels"),
            "importance_score": record.get("importance_score"),
            "generated_code": record.get("generated_code", False),
            "generated_code_reasons": record.get("generated_code_reasons", []),
        })
    return embedding_records


def build_context_records(detail_records: list[dict]) -> list[dict]:
    context_records: list[dict] = []
    for record in detail_records:
        payload = dict(record)
        payload.pop("embedding_text", None)
        context_records.append(payload)
    return context_records


def build_graph_nodes(
    index_records: list[dict],
    detail_records: list[dict],
    mode: str = "jsonl",
) -> list[dict]:
    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for record in [*index_records, *detail_records]:
        node_id = record.get("id")
        if not node_id or node_id in seen_ids:
            continue
        seen_ids.add(node_id)
        if mode == "neo4j":
            nodes.append({
                "id": node_id,
                "labels": [record.get("kind", "Record")],
                "properties": _graph_node_properties(record),
            })
            continue
        nodes.append({
            "id": node_id,
            "kind": record.get("kind"),
            "file": record.get("file"),
            "parent_id": record.get("parent_id"),
            "role_labels": record.get("role_labels"),
            "importance_score": record.get("importance_score"),
            "generated_code": record.get("generated_code", False),
        })
    return nodes


def build_graph_edges(
    index_records: list[dict],
    detail_records: list[dict],
    relation_records: list[dict],
    mode: str = "jsonl",
) -> list[dict]:
    edges: list[dict] = []
    for record in [*index_records, *detail_records]:
        if not record.get("id") or not record.get("parent_id"):
            continue
        if mode == "neo4j":
            edges.append({
                "source": record.get("parent_id"),
                "target": record.get("id"),
                "type": "HAS_CHILD",
                "properties": {"kind": "parent_child"},
            })
            continue
        edges.append({
            "source": record.get("parent_id"),
            "target": record.get("id"),
            "type": "parent_child",
            "kind": "parent_child",
        })

    for record in relation_records:
        source_id = record.get("from")
        target_id = record.get("to")
        if not source_id or not target_id:
            continue
        if mode == "neo4j":
            edges.append({
                "source": source_id,
                "target": target_id,
                "type": str(record.get("type", "RELATED_TO")).upper(),
                "properties": _graph_edge_properties(record),
            })
            continue
        edges.append({
            "source": source_id,
            "target": target_id,
            "type": record.get("type"),
            "kind": "relation",
            "confidence": record.get("confidence"),
            "heuristic": record.get("heuristic"),
        })
    return edges


def _graph_node_properties(record: dict) -> dict:
    return {
        key: value
        for key, value in record.items()
        if key not in {"id", "kind"} and value is not None
    }


def _graph_edge_properties(record: dict) -> dict:
    return {
        key: value
        for key, value in record.items()
        if key not in {"from", "to", "type"} and value is not None
    }
