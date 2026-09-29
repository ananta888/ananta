from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.retrieval.codecompass_architecture_query import (
    QueryLimits,
    classify_result_role,
    render_query_result_markdown,
    resolve_seed,
    run_architecture_query,
    score_evidence_path,
)
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore

_FIXTURE_PATH = Path("tests/fixtures/codecompass_architecture/graph_records.json")

_DTO_ID = "java_type:src/main/java/example/UserDto.java:UserDto"
_SERVICE_ID = "java_type:src/main/java/example/UserService.java:UserService"
_CONTROLLER_ID = "java_type:src/main/java/example/UserController.java:UserController"
_REPOSITORY_ID = "java_type:src/main/java/example/UserRepository.java:UserRepository"
_MAPPER_ID = "java_type:src/main/java/example/UserMapper.java:UserMapper"
_CONTROLLER_TEST_ID = "java_type:src/test/java/example/UserControllerTest.java:UserControllerTest"
_SERVICE_TEST_ID = "java_type:src/test/java/example/UserServiceTest.java:UserServiceTest"
_API_IT_ID = "java_type:src/test/java/example/UserApiIT.java:UserApiIT"
_POLICY_ID = "java_type:src/main/java/example/security/PriceFieldPolicy.java:PriceFieldPolicy"
_FRONTEND_GUARD_ID = "ts_file:frontend/src/app/user-form.guard.ts:UserFormGuard"


@pytest.fixture(params=["json", "sqlite"])
def store(request, tmp_path) -> CodeCompassGraphStore:
    """CCAQE-022: every engine test runs against both store backends."""
    fixture = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    if request.param == "sqlite":
        from worker.retrieval.codecompass_sqlite_graph_store import CodeCompassSqliteGraphStore

        graph_store: CodeCompassGraphStore = CodeCompassSqliteGraphStore(db_path=tmp_path / "cc_graph_index.sqlite")
    else:
        graph_store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    graph_store.rebuild_from_output_records(
        records=fixture["records"],
        manifest_hash=fixture["manifest_hash"],
    )
    return graph_store


def _result_by_node(payload: dict, node_id: str) -> dict | None:
    for entry in payload["results"]:
        if entry["result_node_id"] == node_id:
            return entry
    return None


# --- CCAQE-005: seed resolution -------------------------------------------------


def test_seed_exact_node_id_is_resolved_without_fts(store):
    def _fts_must_not_be_called(query):
        raise AssertionError("fts must not be called for exact node ids")

    resolution = resolve_seed(store=store, seed=_DTO_ID, fts_search=_fts_must_not_be_called)
    assert resolution["resolved_node_ids"] == [_DTO_ID]
    assert resolution["candidates"][0]["reason"] == "node_id_exact"
    assert resolution["warnings"] == []


def test_seed_exact_class_name_is_resolved_via_name_index(store):
    resolution = resolve_seed(store=store, seed="UserDto")
    assert resolution["resolved_node_ids"] == [_DTO_ID]
    assert resolution["candidates"][0]["reason"] == "name_exact"


def test_seed_path_fragment_resolves_via_file_index(store):
    resolution = resolve_seed(store=store, seed="example/UserService.java")
    assert _SERVICE_ID in resolution["resolved_node_ids"]
    assert any(candidate["reason"] == "file_fragment" for candidate in resolution["candidates"])


def test_seed_ambiguity_yields_multiple_candidates_and_warning(tmp_path):
    graph_store = CodeCompassGraphStore(index_path=tmp_path / "cc_graph_index.json")
    graph_store.rebuild_from_output_records(
        records=[
            {"id": "a:Dup", "kind": "java_type", "name": "Dup", "file": "src/a/Dup.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"id": "b:Dup", "kind": "java_type", "name": "Dup", "file": "src/b/Dup.java", "_provenance": {"output_kind": "graph_nodes"}},
            {"source": "a:Dup", "target": "b:Dup", "type": "field_type_uses", "_provenance": {"output_kind": "graph_edges"}},
        ],
        manifest_hash="mh-dup",
    )
    resolution = resolve_seed(store=graph_store, seed="Dup")
    assert len(resolution["candidates"]) == 2
    assert "ambiguous_seed" in resolution["warnings"]


def test_seed_not_found_yields_empty_results_and_warning(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="DoesNotExist")
    assert payload["results"] == []
    assert "seed_not_resolved" in payload["warnings"]


def test_seed_fts_fallback_resolves_via_record_id(store):
    def _fts(query):
        return [{"record_id": _DTO_ID}]

    resolution = resolve_seed(store=store, seed="user data transfer object", fts_search=_fts)
    assert resolution["resolved_node_ids"] == [_DTO_ID]
    assert resolution["candidates"][0]["reason"] == "fts_fallback"


# --- CCAQE-006: ranking ----------------------------------------------------------


def test_ranking_shorter_paths_win_at_equal_confidence():
    short = score_evidence_path([
        {"edge_type": "field_type_uses", "confidence": 0.9},
    ])
    long = score_evidence_path([
        {"edge_type": "field_type_uses", "confidence": 0.9},
        {"edge_type": "field_type_uses", "confidence": 0.9},
    ])
    assert short > long


def test_ranking_hard_edges_beat_heuristic_edges_at_equal_depth():
    hard = score_evidence_path([{"edge_type": "field_type_uses", "confidence": 0.9}])
    heuristic = score_evidence_path([{"edge_type": "calls_probable_target", "confidence": 0.9}])
    assert hard > heuristic


def test_ranking_is_stable_between_runs_and_ordered_by_score(store):
    first = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    second = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    # stable between runs
    assert [entry["result_node_id"] for entry in first["results"]] == [
        entry["result_node_id"] for entry in second["results"]
    ]
    assert first["results"] == second["results"]
    # ordered by score (descending)
    scores = [entry["score"] for entry in first["results"]]
    assert scores == sorted(scores, reverse=True)


# --- CCAQE-007: result contract --------------------------------------------------


def test_result_contract_fields_serialization_and_limit_diagnostics(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    # required top-level fields
    for key in ("schema", "query_type", "seed", "results", "diagnostics", "warnings"):
        assert key in payload
    assert payload["schema"] == "codecompass_architecture_query_result.v1"

    # required entry / path / edge fields
    assert payload["results"]
    for entry in payload["results"]:
        for key in ("result_node_id", "result_kind", "result_role", "score", "depth", "evidence_paths"):
            assert key in entry
        for path in entry["evidence_paths"]:
            assert "path_score" in path
            for edge in path["edges"]:
                for key in ("source_id", "target_id", "edge_type", "direction_used", "confidence"):
                    assert key in edge

    # serializes without a custom encoder
    assert json.loads(json.dumps(payload)) == payload

    # CCAQE-008: diagnostics show bounded and applied limits
    assert payload["diagnostics"]["bounded"] is True
    applied = payload["diagnostics"]["applied_limits"]
    for key in ("max_depth", "max_nodes", "max_results", "max_paths_per_result"):
        assert key in applied


def test_result_contract_empty_results_are_valid_and_explained(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="NopeClass")
    assert payload["results"] == []
    assert payload["warnings"]
    assert json.loads(json.dumps(payload)) == payload


# --- CCAQE-008: limits -----------------------------------------------------------


def test_limits_unknown_query_type_is_rejected(store):
    payload = run_architecture_query(store=store, query_type="free-cypher", seed="UserDto")
    assert payload["error"] == "invalid_query_type"
    assert payload["results"] == []
    assert "dto-impact" in payload["valid_query_types"]


def test_limits_depth_is_clamped_to_configured_max(store):
    payload = run_architecture_query(
        store=store,
        query_type="dto-impact",
        seed="UserDto",
        depth=99,
        limits=QueryLimits(max_depth=2),
    )
    assert payload["diagnostics"]["depth_used"] == 2
    assert "depth_clamped_to_max" in payload["warnings"]


def test_limits_max_results_truncates_all_query_types(store):
    payload = run_architecture_query(
        store=store,
        query_type="dto-impact",
        seed="UserDto",
        limits=QueryLimits(max_results=1),
    )
    assert len(payload["results"]) == 1
    assert "results_truncated_by_max_results" in payload["warnings"]


# --- CCAQE-009: dto-impact -------------------------------------------------------


def test_dto_impact_finds_dependents_with_roles_depths_and_evidence(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")

    # direct service hit via field_type_uses
    service = _result_by_node(payload, _SERVICE_ID)
    assert service is not None
    assert service["result_role"] == "service"
    assert service["depth"] == 1
    assert service["evidence_paths"][0]["edges"][0]["edge_type"] == "field_type_uses"

    # controller is found indirectly
    controller = _result_by_node(payload, _CONTROLLER_ID)
    assert controller is not None
    assert controller["depth"] == 2
    edge_types = {edge["edge_type"] for path in controller["evidence_paths"] for edge in path["edges"]}
    assert "injects_dependency" in edge_types

    # mapper and repository keep their roles
    mapper = _result_by_node(payload, _MAPPER_ID)
    repository = _result_by_node(payload, _REPOSITORY_ID)
    assert mapper is not None and mapper["result_role"] == "mapper"
    assert repository is not None and repository["result_role"] == "repository"

    # evidence paths show the direction used
    edge = service["evidence_paths"][0]["edges"][0]
    assert edge["direction_used"] == "incoming"
    assert edge["source_id"] == _SERVICE_ID
    assert edge["target_id"] == _DTO_ID

    # heuristic-only results carry a warning
    assert repository is not None
    assert "heuristic_evidence_only" not in _result_by_node(payload, _SERVICE_ID)["warnings"]
    edge_types = {edge["edge_type"] for path in repository["evidence_paths"] for edge in path["edges"]}
    assert "calls_probable_target" in edge_types
    assert "calls_probable_target edges are heuristic" in payload["warnings"]


# --- CCAQE-010: controller-test-coverage -----------------------------------------


def test_controller_test_coverage_classifies_direct_endpoint_and_indirect_tests(store):
    payload = run_architecture_query(store=store, query_type="controller-test-coverage", seed="UserController", direction="both")

    # direct controller test is recognized
    direct = _result_by_node(payload, _CONTROLLER_TEST_ID)
    assert direct is not None
    assert direct["coverage_kind"] == "direct_controller_test"
    assert direct["result_role"] == "test"

    # endpoint test is recognized
    endpoint_test = _result_by_node(payload, _API_IT_ID)
    assert endpoint_test is not None
    assert endpoint_test["coverage_kind"] == "endpoint_test"

    # indirect service test is ranked lower and warned
    indirect = _result_by_node(payload, _SERVICE_TEST_ID)
    assert indirect is not None
    assert indirect["score"] < direct["score"]
    assert "no_direct_test_evidence" in indirect["warnings"]
    assert indirect["coverage_kind"] in {"indirect_evidence", "suspected_coverage"}

    # only test results are returned
    assert payload["results"]
    assert all(entry["result_role"] == "test" for entry in payload["results"])
    assert all(entry["coverage_kind"] != "covered" for entry in payload["results"])


def test_controller_test_coverage_depth_three_is_supported_and_diagnosed(store):
    payload = run_architecture_query(store=store, query_type="controller-test-coverage", seed="UserController", depth=3, direction="both")
    assert payload["diagnostics"]["depth_used"] == 3


# --- CCAQE-011: field-policy-impact ----------------------------------------------


def test_field_policy_impact_price_separates_backend_and_frontend_enforcement(store):
    payload = run_architecture_query(store=store, query_type="field-policy-impact", seed="UserDto", field="price")

    # backend policy is an enforced backend guard
    policy = _result_by_node(payload, _POLICY_ID)
    assert policy is not None
    assert policy["enforcement"] == "enforced_backend_guard"
    assert "update" in policy.get("operations", [])

    # frontend guard is not backend enforcement
    guard = _result_by_node(payload, _FRONTEND_GUARD_ID)
    assert guard is not None
    assert guard["enforcement"] == "frontend_reference"

    # all results have evidence and confidence
    assert payload["results"]
    for entry in payload["results"]:
        assert entry["evidence_paths"]
        for path in entry["evidence_paths"]:
            assert all("confidence" in edge for edge in path["edges"])

    # CCAQE-015: security edges propagate source_file and source_record_id so
    # agents can audit WHERE a policy/permission statement came from
    backend_edges = [
        edge
        for path in policy["evidence_paths"]
        for edge in path["edges"]
        if edge.get("edge_type") in {"permission_checks_field", "policy_applies_to_field"}
    ]
    assert backend_edges, "expected at least one backend-enforcement edge"
    for edge in backend_edges:
        assert edge.get("source_file"), f"missing source_file on {edge.get('edge_type')}"
        assert edge.get("source_record_id"), f"missing source_record_id on {edge.get('edge_type')}"
        assert "PriceFieldPolicy" in edge["source_file"]

    # CCAQE-015: frontend_guard_refs_field edges are tagged with enforcement_scope=frontend_only
    guard_edges = [edge for path in guard["evidence_paths"] for edge in path["edges"]]
    assert any(edge.get("edge_type") == "frontend_guard_refs_field" for edge in guard_edges)
    for edge in guard_edges:
        if edge.get("edge_type") == "frontend_guard_refs_field":
            assert edge.get("enforcement_scope") == "frontend_only"
            assert edge.get("source_file") == "frontend/src/app/user-form.guard.ts"


def test_field_policy_impact_field_filter_excludes_other_fields(store):
    payload = run_architecture_query(store=store, query_type="field-policy-impact", seed="UserDto", field="name")
    assert _result_by_node(payload, _POLICY_ID) is None
    assert _result_by_node(payload, _FRONTEND_GUARD_ID) is None


# --- CCAQE-012: service-dependency-chain ------------------------------------------


def test_service_dependency_chain_marks_dependencies_roles_boundary_and_cycles(store):
    payload = run_architecture_query(store=store, query_type="service-dependency-chain", seed="UserService")
    # direct dependencies are marked
    repository = _result_by_node(payload, _REPOSITORY_ID)
    mapper = _result_by_node(payload, _MAPPER_ID)
    assert repository is not None and repository["dependency_kind"] == "direct_dependency"
    assert mapper is not None and mapper["dependency_kind"] == "direct_dependency"
    # repository role and transactional boundary
    assert repository["result_role"] == "repository"
    assert repository.get("transactional_boundary") is True
    # cycles are detected in diagnostics
    assert payload["diagnostics"].get("service_dependency_cycles_detected", 0) >= 1


# --- CCAQE-016: role classification ----------------------------------------------


def test_role_classification_prefers_explicit_role_labels():
    node = {"name": "Whatever", "kind": "java_type", "file": "src/x.java", "source_record": {"role_labels": ["service"]}}
    assert classify_result_role(node) == "service"


def test_role_classification_detects_tests_before_labels():
    node = {"name": "UserControllerTest", "kind": "java_type", "file": "src/test/java/UserControllerTest.java", "source_record": {"role_labels": ["controller"]}}
    assert classify_result_role(node) == "test"


# --- CCAQE-019: markdown handoff --------------------------------------------------


def test_markdown_handoff_contains_query_seed_results_evidence_and_warnings(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    markdown = render_query_result_markdown(payload)
    assert "dto-impact" in markdown
    assert "UserDto" in markdown
    assert _SERVICE_ID in markdown
    assert "Evidence" in markdown
    assert "heuristic" in markdown


def test_markdown_handoff_keeps_security_warnings(store):
    payload = run_architecture_query(store=store, query_type="field-policy-impact", seed="UserDto", field="price")
    payload["warnings"].append("security_review_required")
    markdown = render_query_result_markdown(payload)
    assert "security_review_required" in markdown
    assert "enforcement: enforced_backend_guard" in markdown
    assert "enforcement: frontend_reference" in markdown


def test_markdown_handoff_renders_empty_results_as_not_proven(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="DoesNotExist")
    markdown = render_query_result_markdown(payload)
    assert "nicht gefunden / nicht belegt" in markdown
    assert "seed_not_resolved" in markdown


def test_role_classification_annotation_and_name_heuristics():
    assert classify_result_role({"name": "X", "kind": "java_type", "file": "s.java", "source_record": {"annotations": ["@RestController"]}}) == "controller"
    assert classify_result_role({"name": "AccountRepository", "kind": "java_type", "file": "s.java", "source_record": {}}) == "repository"
    assert classify_result_role({"name": "PriceMapper", "kind": "java_type", "file": "s.java", "source_record": {}}) == "mapper"
    assert classify_result_role({"name": "BillingServiceImpl", "kind": "java_type", "file": "s.java", "source_record": {}}) == "service"
    assert classify_result_role({"name": "AppConfig", "kind": "java_type", "file": "s.java", "source_record": {}}) == "config"


# --- CCAQE-015: security-edge provenance + enforcement_scope ---


def test_non_security_edges_are_not_annotated():
    """Provenance annotation is scoped to security-relevant edge types only."""
    from worker.retrieval.codecompass_architecture_query import _annotate_security_provenance

    plain_edges = [
        {"edge_type": "field_type_uses", "source_id": "a", "target_id": "b", "confidence": 0.9},
        {"edge_type": "injects_dependency", "source_id": "c", "target_id": "d", "confidence": 0.8},
    ]
    annotated = _annotate_security_provenance(plain_edges, source_nodes={})
    for edge in annotated:
        assert "source_file" not in edge
        assert "source_record_id" not in edge
        assert "enforcement_scope" not in edge


def test_security_edge_provenance_falls_back_gracefully_without_source_nodes():
    """Missing source-node lookup must not fabricate a source_file."""
    from worker.retrieval.codecompass_architecture_query import _annotate_security_provenance

    edges = [{"edge_type": "permission_checks_field", "source_id": "missing", "target_id": "x", "confidence": 0.9}]
    annotated = _annotate_security_provenance(edges, source_nodes={})
    assert annotated[0]["source_file"] is None
    assert annotated[0]["source_record_id"] is None
