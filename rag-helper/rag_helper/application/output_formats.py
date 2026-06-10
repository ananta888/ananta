from __future__ import annotations


_CONTEXT_DROP_KEYS = {
    "embedding_text",
    "code_snippet",
    "body",
    "raw_text",
    "source",
}
_CONTEXT_TRUNCATE_KEYS = {
    "documentation": 300,
    "documentation_summary": 200,
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
    "attributes": 12,
    "decorator_texts": 8,
    "frameworks": 8,
    "framework_roles": 8,
    "frontend_artifacts": 12,
    "hook_calls": 12,
    "properties": 20,
    "usings": 20,
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


def _build_graph_resolution_maps(
    index_records: list[dict],
    detail_records: list[dict],
    relation_records: list[dict],
) -> tuple[set[str], dict[str, str], dict[str, str], dict[str, str]]:
    """CCAQE-013/014: lookup maps so symbol relations become real graph edges.

    Returns known node ids, FQN->node_id, unique simple name->node_id and
    endpoint path->method node_id (from controller_endpoint_declares relations).
    """
    node_ids: set[str] = set()
    by_fqn: dict[str, str] = {}
    simple_candidates: dict[str, list[str]] = {}
    for record in [*index_records, *detail_records]:
        record_id = record.get("id")
        if not record_id:
            continue
        node_ids.add(record_id)
        name = record.get("name")
        if not name:
            continue
        package = record.get("package")
        if package:
            by_fqn.setdefault(f"{package}.{name}", record_id)
        kind = str(record.get("kind") or "")
        if kind in {"java_type", "csharp_type"}:
            simple_candidates.setdefault(str(name), []).append(record_id)
    by_simple_name = {
        name: candidates[0]
        for name, candidates in simple_candidates.items()
        if len(set(candidates)) == 1
    }
    endpoint_paths: dict[str, str] = {}
    for record in relation_records:
        if str(record.get("relation") or record.get("type") or "") != "controller_endpoint_declares":
            continue
        path = str(record.get("endpoint_path") or "").strip()
        method_id = str(record.get("target_resolved") or "").strip()
        if path and method_id:
            endpoint_paths.setdefault(path, method_id)
    return node_ids, by_fqn, by_simple_name, endpoint_paths


def _resolve_graph_node_id(
    value: str | None,
    node_ids: set[str],
    by_fqn: dict[str, str],
    by_simple_name: dict[str, str],
) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    if candidate in node_ids:
        return candidate
    if candidate in by_fqn:
        return by_fqn[candidate]
    if candidate in by_simple_name:
        return by_simple_name[candidate]
    simple = candidate.rsplit(".", 1)[-1]
    if simple != candidate and simple in by_simple_name:
        return by_simple_name[simple]
    return None


def build_graph_edges(
    index_records: list[dict],
    detail_records: list[dict],
    relation_records: list[dict],
    mode: str = "jsonl",
) -> list[dict]:
    edges: list[dict] = []
    seen_edge_keys: set[tuple] = set()

    def _append_jsonl_edge(source: str, target: str, edge_type: str, record: dict) -> None:
        key = (source, target, edge_type)
        if key in seen_edge_keys:
            return
        seen_edge_keys.add(key)
        edge = {
            "source": source,
            "target": target,
            "type": edge_type,
            "kind": "relation",
            "confidence": record.get("confidence"),
            "heuristic": record.get("heuristic"),
        }
        for attribute_key in ("field", "operation", "endpoint_path", "http_method"):
            if record.get(attribute_key) is not None:
                edge[attribute_key] = record[attribute_key]
        edges.append(edge)

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

    node_ids, by_fqn, by_simple_name, endpoint_paths = _build_graph_resolution_maps(
        index_records, detail_records, relation_records
    )

    for record in relation_records:
        source_id = record.get("from")
        target_id = record.get("to")
        if source_id and target_id:
            if mode == "neo4j":
                edges.append({
                    "source": source_id,
                    "target": target_id,
                    "type": str(record.get("type", "RELATED_TO")).upper(),
                    "properties": _graph_edge_properties(record),
                })
                continue
            _append_jsonl_edge(str(source_id), str(target_id), str(record.get("type") or "related"), record)
            continue

        # Symbol relations from the language extractors (make_relation format):
        # resolve source/target to node ids; unresolvable references stay out of
        # the graph so traversal never follows dangling edges.
        relation_type = str(record.get("relation") or "").strip()
        if not relation_type:
            continue
        resolved_source = _resolve_graph_node_id(record.get("source_id"), node_ids, by_fqn, by_simple_name)
        if resolved_source is None:
            continue
        if relation_type == "test_calls_endpoint":
            resolved_target = endpoint_paths.get(str(record.get("target") or "").strip())
        else:
            resolved_target = _resolve_graph_node_id(
                record.get("target_resolved"), node_ids, by_fqn, by_simple_name
            ) or _resolve_graph_node_id(record.get("target"), node_ids, by_fqn, by_simple_name)
        if resolved_target is None or resolved_target == resolved_source:
            continue
        if mode == "neo4j":
            edges.append({
                "source": resolved_source,
                "target": resolved_target,
                "type": relation_type.upper(),
                "properties": _graph_edge_properties(record),
            })
            continue
        _append_jsonl_edge(resolved_source, resolved_target, relation_type, record)
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
