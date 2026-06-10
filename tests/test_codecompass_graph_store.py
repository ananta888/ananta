from __future__ import annotations

from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore


def test_codecompass_graph_store_loads_nodes_edges_and_indexes(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    diagnostics = store.rebuild_from_output_records(
        records=[
            {
                "id": "n1",
                "kind": "java_type",
                "name": "PaymentService",
                "file": "src/PaymentService.java",
                "_provenance": {"output_kind": "graph_nodes"},
            },
            {
                "id": "n2",
                "kind": "java_method",
                "name": "retryTimeout",
                "file": "src/PaymentService.java",
                "_provenance": {"output_kind": "graph_nodes"},
            },
            {
                "source": "n2",
                "target": "n1",
                "type": "child_of_type",
                "_provenance": {"output_kind": "graph_edges"},
            },
        ],
        manifest_hash="mh-1",
    )
    loaded = store.load()
    traversal = store.traverse(seed_ids=["n2"], max_depth=2, max_nodes=5, allowed_edge_types={"child_of_type"})

    assert diagnostics["status"] == "ready"
    by_id = dict((loaded["node_index"] or {}).get("by_id") or {})
    assert "n1" in by_id and "n2" in by_id
    assert loaded["outgoing_index"]["n2"]["child_of_type"][0]["target_id"] == "n1"
    assert loaded["incoming_index"]["n1"]["child_of_type"][0]["source_id"] == "n2"
    assert traversal["cycle_guarded"] is True
    assert traversal["bounded"] is True
    assert [node["id"] for node in traversal["nodes"]] == ["n2", "n1"]


def _build_sample_store(tmp_path) -> CodeCompassGraphStore:
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    store.rebuild_from_output_records(
        records=[
            {"id": "type:UserDto", "kind": "java_type", "name": "UserDto", "file": "src/UserDto.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"id": "type:UserService", "kind": "java_type", "name": "UserService", "file": "src/UserService.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"id": "type:UserController", "kind": "java_type", "name": "UserController", "file": "src/UserController.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"source": "type:UserService", "target": "type:UserDto", "type": "field_type_uses", "confidence": 0.94, "_provenance": {"output_kind": "graph_edges"}},
            {"source": "type:UserController", "target": "type:UserService", "type": "injects_dependency", "confidence": 0.9, "_provenance": {"output_kind": "graph_edges"}},
            {"source": "type:UserService", "target": "type:UserController", "type": "calls_probable_target", "confidence": 0.4, "_provenance": {"output_kind": "graph_edges"}},
        ],
        manifest_hash="mh-sample",
    )
    return store


def test_incoming_edges_returns_edges_from_incoming_index(tmp_path):
    store = _build_sample_store(tmp_path)
    edges = store.incoming_edges(node_id="type:UserDto")
    assert len(edges) == 1
    assert edges[0]["source_id"] == "type:UserService"
    assert edges[0]["edge_type"] == "field_type_uses"


def test_find_nodes_by_name_supports_exact_and_case_insensitive_fallback(tmp_path):
    store = _build_sample_store(tmp_path)
    exact = store.find_nodes_by_name(name="UserDto")
    fallback = store.find_nodes_by_name(name="userdto")
    assert [node["id"] for node in exact] == ["type:UserDto"]
    assert [node["id"] for node in fallback] == ["type:UserDto"]


def test_find_nodes_by_file_supports_exact_and_fragment(tmp_path):
    store = _build_sample_store(tmp_path)
    exact = store.find_nodes_by_file(file="src/UserService.java")
    fragment = store.find_nodes_by_file(file="userservice")
    assert [node["id"] for node in exact] == ["type:UserService"]
    assert [node["id"] for node in fragment] == ["type:UserService"]


def test_lookup_methods_return_empty_results_when_index_missing(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "missing.json")
    assert store.get_node(node_id="type:UserDto") is None
    assert store.find_nodes_by_name(name="UserDto") == []
    assert store.find_nodes_by_file(file="src/UserDto.java") == []
    assert store.incoming_edges(node_id="type:UserDto") == []
    assert store.outgoing_edges(node_id="type:UserDto") == []
    traversal = store.traverse_paths(seed_ids=["type:UserDto"], max_depth=2, max_nodes=5)
    assert traversal["nodes"] == [] and traversal["paths"] == []


def test_traverse_paths_incoming_finds_nodes_pointing_at_seed(tmp_path):
    store = _build_sample_store(tmp_path)
    traversal = store.traverse_paths(seed_ids=["type:UserDto"], max_depth=2, max_nodes=10, direction="incoming")
    node_ids = [node["id"] for node in traversal["nodes"]]
    assert "type:UserService" in node_ids
    assert "type:UserController" in node_ids
    service_paths = next(row for row in traversal["paths"] if row["node_id"] == "type:UserService")
    edge = service_paths["evidence_paths"][0]["edges"][0]
    assert edge["edge_type"] == "field_type_uses"
    assert edge["source_id"] == "type:UserService"
    assert edge["target_id"] == "type:UserDto"
    assert edge["direction_used"] == "incoming"
    assert edge["confidence"] == 0.94


def test_traverse_paths_outgoing_stays_compatible(tmp_path):
    store = _build_sample_store(tmp_path)
    traversal = store.traverse_paths(seed_ids=["type:UserController"], max_depth=2, max_nodes=10, direction="outgoing")
    node_ids = [node["id"] for node in traversal["nodes"]]
    assert node_ids[0] == "type:UserController"
    assert "type:UserService" in node_ids and "type:UserDto" in node_ids


def test_traverse_paths_both_dedupes_nodes_and_guards_cycles(tmp_path):
    store = _build_sample_store(tmp_path)
    traversal = store.traverse_paths(seed_ids=["type:UserService"], max_depth=3, max_nodes=10, direction="both")
    node_ids = [node["id"] for node in traversal["nodes"]]
    assert len(node_ids) == len(set(node_ids))
    assert traversal["cycle_guarded"] is True
    assert traversal["bounded"] is True
    first = store.traverse_paths(seed_ids=["type:UserService"], max_depth=3, max_nodes=10, direction="both")
    assert first["paths"] == traversal["paths"]


def test_traverse_paths_terminates_on_cyclic_graph(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    store.rebuild_from_output_records(
        records=[
            {"id": "a", "kind": "java_type", "name": "A", "file": "src/A.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"id": "b", "kind": "java_type", "name": "B", "file": "src/B.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"source": "a", "target": "b", "type": "calls_probable_target", "_provenance": {"output_kind": "graph_edges"}},
            {"source": "b", "target": "a", "type": "calls_probable_target", "_provenance": {"output_kind": "graph_edges"}},
        ],
        manifest_hash="mh-cycle",
    )
    traversal = store.traverse_paths(seed_ids=["a"], max_depth=5, max_nodes=10, direction="both")
    assert traversal["cycle_count"] >= 1
    assert [node["id"] for node in traversal["nodes"]] == ["a", "b"]


def test_sqlite_store_delivers_equivalent_results_to_json_store(tmp_path):
    """CCAQE-022: both backends fulfil the same contract on the same graph."""
    from worker.retrieval.codecompass_sqlite_graph_store import CodeCompassSqliteGraphStore

    json_store = _build_sample_store(tmp_path)
    sqlite_store = CodeCompassSqliteGraphStore(db_path=tmp_path / "cc_graph_index.sqlite")
    sqlite_store.rebuild_from_output_records(
        records=[
            {"id": "type:UserDto", "kind": "java_type", "name": "UserDto", "file": "src/UserDto.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"id": "type:UserService", "kind": "java_type", "name": "UserService", "file": "src/UserService.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"id": "type:UserController", "kind": "java_type", "name": "UserController", "file": "src/UserController.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"source": "type:UserService", "target": "type:UserDto", "type": "field_type_uses", "confidence": 0.94, "_provenance": {"output_kind": "graph_edges"}},
            {"source": "type:UserController", "target": "type:UserService", "type": "injects_dependency", "confidence": 0.9, "_provenance": {"output_kind": "graph_edges"}},
            {"source": "type:UserService", "target": "type:UserController", "type": "calls_probable_target", "confidence": 0.4, "_provenance": {"output_kind": "graph_edges"}},
        ],
        manifest_hash="mh-sample",
    )

    assert sqlite_store.incoming_edges(node_id="type:UserDto") == json_store.incoming_edges(node_id="type:UserDto")
    assert sqlite_store.outgoing_edges(node_id="type:UserService") == json_store.outgoing_edges(node_id="type:UserService")
    assert sqlite_store.find_nodes_by_name(name="UserDto") == json_store.find_nodes_by_name(name="UserDto")
    json_traversal = json_store.traverse_paths(seed_ids=["type:UserDto"], max_depth=3, max_nodes=10, direction="both")
    sqlite_traversal = sqlite_store.traverse_paths(seed_ids=["type:UserDto"], max_depth=3, max_nodes=10, direction="both")
    assert sqlite_traversal["paths"] == json_traversal["paths"]
    assert [node["id"] for node in sqlite_traversal["nodes"]] == [node["id"] for node in json_traversal["nodes"]]


def test_sqlite_store_degrades_when_database_missing(tmp_path):
    from worker.retrieval.codecompass_sqlite_graph_store import CodeCompassSqliteGraphStore

    store = CodeCompassSqliteGraphStore(db_path=tmp_path / "missing.sqlite")
    payload = store.load()
    assert payload["diagnostics"]["status"] == "degraded"
    assert store.find_nodes_by_name(name="UserDto") == []


def test_codecompass_graph_store_degrades_when_graph_outputs_missing(tmp_path):
    store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    diagnostics = store.rebuild_from_output_records(
        records=[
            {
                "id": "n1",
                "kind": "java_type",
                "name": "PaymentService",
                "file": "src/PaymentService.java",
                "_provenance": {"output_kind": "graph_nodes"},
            }
        ],
        manifest_hash="mh-1",
    )

    assert diagnostics["status"] == "degraded"
    assert diagnostics["reason"] == "missing_graph_outputs"

