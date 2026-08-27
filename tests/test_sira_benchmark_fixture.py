from __future__ import annotations

import json
from pathlib import Path

from worker.retrieval.codecompass_fts_store import CodeCompassFtsStore
from worker.retrieval.sira.config import SiraConfig
from worker.retrieval.sira.contracts import CorpusBinding, GeneratedTerm
from worker.retrieval.sira.enriched_fts_store import EnrichedFtsStore
from worker.retrieval.sira.term_validator import CorpusTermValidator
from worker.retrieval.sira.weighted_query_compiler import WeightedQueryCompiler, tokenize_original_query

ROOT = Path(__file__).resolve().parents[1]


def test_sira_baseline_is_reproducible_from_bound_fixture(tmp_path: Path):
    golden = json.loads((ROOT / "tests/fixtures/retrieval/sira_golden_queries.json").read_text(encoding="utf-8"))
    baseline = json.loads((ROOT / "benchmarks/retrieval/sira_baseline.json").read_text(encoding="utf-8"))
    assert baseline["binding"] == golden["binding"]
    store = CodeCompassFtsStore(db_path=tmp_path / "baseline.sqlite")
    store.rebuild(documents=golden["corpus"], retrieval_cache_state="sira-fixture-v1")
    expected = {item["query_id"]: [row["record_id"] for row in item["records"]] for item in baseline["queries"]}
    for query in golden["queries"]:
        rows = store.search(query=query["query"], top_k=10)
        assert [row["record_id"] for row in rows] == expected[query["query_id"]]


def test_sira_candidate_is_reproducible_and_keeps_one_lexical_action(tmp_path: Path):
    golden = json.loads((ROOT / "tests/fixtures/retrieval/sira_golden_queries.json").read_text(encoding="utf-8"))
    candidate = json.loads((ROOT / "benchmarks/retrieval/sira_candidate.json").read_text(encoding="utf-8"))
    assert candidate["binding"] == golden["binding"]
    binding = CorpusBinding(
        tenant_id="fixture-tenant",
        scope="fixture-repo",
        repository_revision="sira-fixture-v1",
        source_manifest_hash="sira-fixture-manifest-v1",
        index_digest="sira-fixture-index-v1",
        statistics_digest="sira-fixture-statistics-v1",
        profile_version="corpus-discriminative-lexical.v1",
    )
    store = EnrichedFtsStore(db_path=tmp_path / "candidate.sqlite")
    store.rebuild(
        documents=golden["corpus"],
        enrichments={
            "payment.retry": {
                "generated_terms": [
                    {"value": "backoff declined charge"},
                    {"value": "Zahlung erneut versuchen"},
                ]
            },
            "hub.dispatch": {"generated_terms": [{"value": "control plane execution boundary"}]},
        },
        binding=binding,
    )
    expected = {item["query_id"]: [row["record_id"] for row in item["records"]] for item in candidate["queries"]}
    validator = CorpusTermValidator(statistics=store, config=SiraConfig())
    compiler = WeightedQueryCompiler()
    expansions = {
        "bugfix-vocabulary-gap": [GeneratedTerm("backoff declined charge", 0.9)],
        "architecture-vocabulary-gap": [GeneratedTerm("control plane execution boundary", 0.9)],
        "cross-language": [GeneratedTerm("Zahlung erneut versuchen", 0.9)],
    }
    for query in golden["queries"]:
        decisions = validator.validate(
            expansions.get(query["query_id"], []),
            binding=binding,
            original_terms=tokenize_original_query(query["query"]),
        )
        compiled = compiler.compile(original_query=query["query"], decisions=decisions, binding=binding)
        rows = store.search_weighted(compiled, top_k=10)
        assert [row["record_id"] for row in rows] == expected[query["query_id"]]
