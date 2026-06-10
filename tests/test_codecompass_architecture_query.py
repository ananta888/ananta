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


def test_ranking_is_stable_between_runs(store):
    first = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    second = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    assert [entry["result_node_id"] for entry in first["results"]] == [
        entry["result_node_id"] for entry in second["results"]
    ]
    assert first["results"] == second["results"]


def test_ranking_orders_results_by_score_then_node_id(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    scores = [entry["score"] for entry in payload["results"]]
    assert scores == sorted(scores, reverse=True)


# --- CCAQE-007: result contract --------------------------------------------------


def test_result_contract_contains_required_top_level_fields(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    for key in ("schema", "query_type", "seed", "results", "diagnostics", "warnings"):
        assert key in payload
    assert payload["schema"] == "codecompass_architecture_query_result.v1"


def test_result_contract_entries_have_required_fields(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    assert payload["results"]
    for entry in payload["results"]:
        for key in ("result_node_id", "result_kind", "result_role", "score", "depth", "evidence_paths"):
            assert key in entry
        for path in entry["evidence_paths"]:
            assert "path_score" in path
            for edge in path["edges"]:
                for key in ("source_id", "target_id", "edge_type", "direction_used", "confidence"):
                    assert key in edge


def test_result_contract_serializes_without_custom_encoder(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    assert json.loads(json.dumps(payload)) == payload


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


def test_limits_diagnostics_show_bounded_and_applied_limits(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    assert payload["diagnostics"]["bounded"] is True
    applied = payload["diagnostics"]["applied_limits"]
    for key in ("max_depth", "max_nodes", "max_results", "max_paths_per_result"):
        assert key in applied


# --- CCAQE-009: dto-impact -------------------------------------------------------


def test_dto_impact_direct_service_hit_via_field_type_uses(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    service = _result_by_node(payload, _SERVICE_ID)
    assert service is not None
    assert service["result_role"] == "service"
    assert service["depth"] == 1
    assert service["evidence_paths"][0]["edges"][0]["edge_type"] == "field_type_uses"


def test_dto_impact_controller_is_found_indirectly(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    controller = _result_by_node(payload, _CONTROLLER_ID)
    assert controller is not None
    assert controller["depth"] == 2
    edge_types = {edge["edge_type"] for path in controller["evidence_paths"] for edge in path["edges"]}
    assert "injects_dependency" in edge_types


def test_dto_impact_mapper_and_repository_keep_their_roles(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    mapper = _result_by_node(payload, _MAPPER_ID)
    repository = _result_by_node(payload, _REPOSITORY_ID)
    assert mapper is not None and mapper["result_role"] == "mapper"
    assert repository is not None and repository["result_role"] == "repository"


def test_dto_impact_evidence_paths_show_direction_used(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    service = _result_by_node(payload, _SERVICE_ID)
    edge = service["evidence_paths"][0]["edges"][0]
    assert edge["direction_used"] == "incoming"
    assert edge["source_id"] == _SERVICE_ID
    assert edge["target_id"] == _DTO_ID


def test_dto_impact_heuristic_only_results_carry_warning(store):
    payload = run_architecture_query(store=store, query_type="dto-impact", seed="UserDto")
    repository = _result_by_node(payload, _REPOSITORY_ID)
    assert repository is not None
    assert "heuristic_evidence_only" not in _result_by_node(payload, _SERVICE_ID)["warnings"]
    edge_types = {edge["edge_type"] for path in repository["evidence_paths"] for edge in path["edges"]}
    assert "calls_probable_target" in edge_types
    assert "calls_probable_target edges are heuristic" in payload["warnings"]


# --- CCAQE-010: controller-test-coverage -----------------------------------------


def test_controller_test_coverage_direct_test_is_recognized(store):
    payload = run_architecture_query(store=store, query_type="controller-test-coverage", seed="UserController", direction="both")
    direct = _result_by_node(payload, _CONTROLLER_TEST_ID)
    assert direct is not None
    assert direct["coverage_kind"] == "direct_controller_test"
    assert direct["result_role"] == "test"


def test_controller_test_coverage_endpoint_test_is_recognized(store):
    payload = run_architecture_query(store=store, query_type="controller-test-coverage", seed="UserController", direction="both")
    endpoint_test = _result_by_node(payload, _API_IT_ID)
    assert endpoint_test is not None
    assert endpoint_test["coverage_kind"] == "endpoint_test"


def test_controller_test_coverage_indirect_service_test_ranked_lower_and_warned(store):
    payload = run_architecture_query(store=store, query_type="controller-test-coverage", seed="UserController", direction="both")
    direct = _result_by_node(payload, _CONTROLLER_TEST_ID)
    indirect = _result_by_node(payload, _SERVICE_TEST_ID)
    assert indirect is not None
    assert indirect["score"] < direct["score"]
    assert "no_direct_test_evidence" in indirect["warnings"]
    assert indirect["coverage_kind"] in {"indirect_evidence", "suspected_coverage"}


def test_controller_test_coverage_depth_three_is_supported_and_diagnosed(store):
    payload = run_architecture_query(store=store, query_type="controller-test-coverage", seed="UserController", depth=3, direction="both")
    assert payload["diagnostics"]["depth_used"] == 3


def test_controller_test_coverage_only_test_results_are_returned(store):
    payload = run_architecture_query(store=store, query_type="controller-test-coverage", seed="UserController", direction="both")
    assert payload["results"]
    assert all(entry["result_role"] == "test" for entry in payload["results"])
    assert all(entry["coverage_kind"] != "covered" for entry in payload["results"])


# --- CCAQE-011: field-policy-impact ----------------------------------------------


def test_field_policy_impact_backend_policy_is_enforced_backend_guard(store):
    payload = run_architecture_query(store=store, query_type="field-policy-impact", seed="UserDto", field="price")
    policy = _result_by_node(payload, _POLICY_ID)
    assert policy is not None
    assert policy["enforcement"] == "enforced_backend_guard"
    assert "update" in policy.get("operations", [])


def test_field_policy_impact_frontend_guard_is_not_backend_enforcement(store):
    payload = run_architecture_query(store=store, query_type="field-policy-impact", seed="UserDto", field="price")
    guard = _result_by_node(payload, _FRONTEND_GUARD_ID)
    assert guard is not None
    assert guard["enforcement"] == "frontend_reference"


def test_field_policy_impact_results_have_evidence_and_confidence(store):
    payload = run_architecture_query(store=store, query_type="field-policy-impact", seed="UserDto", field="price")
    assert payload["results"]
    for entry in payload["results"]:
        assert entry["evidence_paths"]
        for path in entry["evidence_paths"]:
            assert all("confidence" in edge for edge in path["edges"])


def test_field_policy_impact_field_filter_excludes_other_fields(store):
    payload = run_architecture_query(store=store, query_type="field-policy-impact", seed="UserDto", field="name")
    assert _result_by_node(payload, _POLICY_ID) is None
    assert _result_by_node(payload, _FRONTEND_GUARD_ID) is None


# --- CCAQE-012: service-dependency-chain ------------------------------------------


def test_service_dependency_chain_direct_dependencies_are_marked(store):
    payload = run_architecture_query(store=store, query_type="service-dependency-chain", seed="UserService")
    repository = _result_by_node(payload, _REPOSITORY_ID)
    mapper = _result_by_node(payload, _MAPPER_ID)
    assert repository is not None and repository["dependency_kind"] == "direct_dependency"
    assert mapper is not None and mapper["dependency_kind"] == "direct_dependency"


def test_service_dependency_chain_marks_repository_role_and_boundary(store):
    payload = run_architecture_query(store=store, query_type="service-dependency-chain", seed="UserService")
    repository = _result_by_node(payload, _REPOSITORY_ID)
    assert repository["result_role"] == "repository"
    assert repository.get("transactional_boundary") is True


def test_service_dependency_chain_detects_cycles_in_diagnostics(store):
    payload = run_architecture_query(store=store, query_type="service-dependency-chain", seed="UserService")
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
