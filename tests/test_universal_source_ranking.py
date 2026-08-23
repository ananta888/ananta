from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from ananta_codecompass.ranking import RankingCandidate, RankingInput, UniversalSourceRanker
from ananta_codecompass.ranking.file_roles import classify_file_role
from ananta_codecompass.ranking.graph_features import derive_graph_features
from agent.services.codecompass_universal_ranking_profile_service import (
    CodeCompassUniversalRankingProfileService,
)


def test_ranking_is_byte_stable_and_contributions_reconstruct_score() -> None:
    ranking_input = RankingInput(
        query="payment route architecture",
        repository_revision="rev-1",
        index_digest="idx-1",
        candidates=(
            RankingCandidate("b", "src/payment/service.py", ("PaymentService",)),
            RankingCandidate("a", "src/payment/routes.py", ("register_payment_route",)),
        ),
    )
    ranker = UniversalSourceRanker()
    first = ranker.rank(ranking_input, top_k=2)
    second = ranker.rank(ranking_input, top_k=2)

    assert first.canonical_json() == second.canonical_json()
    for item in first.ranked:
        assert item.score == round(sum(row.contribution for row in item.contributions), 12)
    assert first.ranked[0].candidate.path == "src/payment/routes.py"
    schema = json.loads(
        Path("schemas/codecompass/universal_source_ranking.v1.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(first.as_dict(), schema)


def test_file_roles_are_language_conventional_and_unknown_directories_neutral() -> None:
    assert classify_file_role("pkg/tests/test_api.py").role == "test"
    assert classify_file_role("web/src/api.spec.ts").role == "test"
    assert classify_file_role("src/test/java/a/ApiTest.java").role == "test"
    assert classify_file_role("target/generated/Api.java", "// generated, do not edit").role in {"build_output", "generated"}
    assert classify_file_role("unfamiliar/subtree/service.py").role == "production"


def test_graph_features_require_evidence_and_mark_partial_coverage() -> None:
    features = derive_graph_features(
        nodes=[{"id": "route"}, {"id": "service"}, {"id": "store"}],
        edges=[
            {"source": "route", "target": "service", "source_ref": "src/routes.py:10"},
            {"source": "service", "target": "store"},
        ],
        query_node_ids={"route"},
    )

    assert features["route"].query_distance == 0
    assert features["service"].query_distance == 1
    assert features["store"].query_distance is None
    assert features["route"].evidence_refs == ("src/routes.py:10",)
    assert features["store"].coverage == "partial"


def test_override_requires_complete_governance_and_future_expiry() -> None:
    service = CodeCompassUniversalRankingProfileService()
    rejected = service.resolve({
        "ANANTA_CODECOMPASS_RANKING_OVERRIDE_JSON": json.dumps({"weights": {"path_lexical": 1.0}}),
    })
    active = service.resolve({
        "ANANTA_CODECOMPASS_REPOSITORY_RANKER": "shadow",
        "ANANTA_CODECOMPASS_RANKING_OVERRIDE_JSON": json.dumps({
            "owner": "ranking-team",
            "reason": "bound evaluation",
            "scope": "deployment",
            "version": "experiment-1",
            "expires_at": "2099-01-01T00:00:00Z",
            "weights": {"path_lexical": 0.25},
        }),
    })

    assert rejected.override_status == "rejected_missing_governance"
    assert rejected.profile.overrides_enabled is False
    assert active.strategy == "shadow"
    assert active.override_status == "active_experimental_override"
    assert active.profile.override_metadata["owner"] == "ranking-team"


def test_multilanguage_golden_set_has_perfect_first_relevant_result() -> None:
    from scripts.evaluate_universal_source_ranking import evaluate

    fixture_path = Path("tests/fixtures/scenarios/universal_source_ranking.v1.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert {repo["language"] for repo in fixture["repositories"]} == {
        "python", "typescript", "java",
    }
    report = evaluate()
    assert all(row["metrics"]["mrr"] == 1.0 for row in report["results"])
    assert all(row["metrics"]["recall@3"] == 1.0 for row in report["results"])
    assert all(row["metrics"]["ndcg@3"] == 1.0 for row in report["results"])


def test_repository_map_shadow_does_not_change_active_selection(monkeypatch) -> None:
    from agent.repository_map_engine import RepositoryMapEngine

    engine = RepositoryMapEngine("/tmp/universal-shadow-test")
    engine._symbol_graph = {
        "src/orders/routes.py": ["register_order_routes"],
        "tests/test_order_routes.py": ["test_order_routes"],
    }
    monkeypatch.setenv("ANANTA_CODECOMPASS_REPOSITORY_RANKER", "shadow")

    selected = engine.search("order routes", top_k=2)
    trace = engine.ranking_trace()

    assert [item.source for item in selected] == trace["shadow"]["universal_paths"]
    assert trace["strategy"] == "shadow"
    assert trace["shadow"]["comparison_digest"]
