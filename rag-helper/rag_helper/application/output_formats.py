from __future__ import annotations


_CONTEXT_DROP_KEYS = {
    "embedding_text",
    "code_snippet",
    "body",
    "raw_text",
    "source",
}
_CONTEXT_TRUNCATE_KEYS = {
    "text": 200,
    "summary": 300,
    "signature": 200,
    "query": 240,
}
_CONTEXT_LIST_LIMIT_KEYS = {
    "calls": 12,
    "resolved_call_targets": 12,
    "used_types_resolved": 16,
    "resolved_type_refs": 16,
    "child_tags": 12,
    "children": 12,
    "attribute_names": 12,
    "annotations": 12,
    "implements": 12,
    "extends": 12,
    "parameters": 12,
    "fields": 20,
    "methods": 20,
    "constructors": 12,
    "tags": 20,
}

_GRAPH_EXCLUDED_NODE_KINDS = {
    "java_package_summary",
}


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


def build_context_records(detail_records: list[dict], mode: str = "full") -> list[dict]:
    context_records: list[dict] = []
    for record in detail_records:
        payload = dict(record)
        if mode == "compact":
            payload = _compact_context_record(payload)
        else:
            payload.pop("embedding_text", None)
        context_records.append(payload)
    return context_records


def _compact_context_record(record: dict) -> dict:
    payload: dict = {}
    for key, value in record.items():
        if key in _CONTEXT_DROP_KEYS:
            continue
        if value is None:
            continue
        if key in _CONTEXT_TRUNCATE_KEYS and isinstance(value, str):
            payload[key] = value[:_CONTEXT_TRUNCATE_KEYS[key]]
            continue
        if key in _CONTEXT_LIST_LIMIT_KEYS and isinstance(value, list):
            payload[key] = _compact_list(value, _CONTEXT_LIST_LIMIT_KEYS[key])
            continue
        if isinstance(value, dict):
            payload[key] = _compact_dict(value)
            continue
        payload[key] = value
    return payload


def _compact_list(values: list, limit: int) -> list:
    compacted = []
    for item in values[:limit]:
        if isinstance(item, dict):
            compacted.append(_compact_dict(item))
        else:
            compacted.append(item)
    return compacted


def _compact_dict(value: dict) -> dict:
    compacted: dict = {}
    for key, nested in value.items():
        if nested is None:
            continue
        if isinstance(nested, str):
            compacted[key] = nested[:160]
            continue
        if isinstance(nested, list):
            compacted[key] = nested[:8]
            continue
        compacted[key] = nested
    return compacted


def build_graph_nodes(
    index_records: list[dict],
    detail_records: list[dict],
    mode: str = "jsonl",
) -> list[dict]:
    nodes: list[dict] = []
    seen_ids: set[str] = set()
    for record in [*index_records, *detail_records]:
        node_id = record.get("id")
        if (
            not node_id
            or node_id in seen_ids
            or record.get("kind") in _GRAPH_EXCLUDED_NODE_KINDS
        ):
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
