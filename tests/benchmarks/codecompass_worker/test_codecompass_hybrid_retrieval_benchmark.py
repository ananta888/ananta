from __future__ import annotations

import json
import re
import time
from pathlib import Path

from worker.retrieval.codecompass_embedding_loader import load_codecompass_embedding_documents
from worker.retrieval.codecompass_graph_context import build_graph_context_chunks
from worker.retrieval.codecompass_graph_expansion import expand_codecompass_graph
from worker.retrieval.codecompass_graph_store import CodeCompassGraphStore
from worker.retrieval.codecompass_vector_engine import CodeCompassVectorEngine
from worker.retrieval.codecompass_vector_store import CodeCompassVectorStore
from worker.retrieval.embedding_provider import FakeEmbeddingProvider
from worker.retrieval.retrieval_service import HybridRetrievalService

_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]+")


def _tokens(value: str) -> set[str]:
    return {item.lower() for item in _TOKEN_RE.findall(str(value or ""))}


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        payload = line.strip()
        if not payload:
            continue
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _lexical_candidates(records: list[dict], query: str, *, boost_exact: bool = False) -> list[dict]:
    query_tokens = _tokens(query)
    candidates = []
    for record in records:
        record_id = str(record.get("id") or "")
        file = str(record.get("file") or record.get("path") or "")
        text = " ".join(
            [
                record_id,
                file,
                str(record.get("summary") or ""),
                str(record.get("content") or ""),
                str(record.get("name") or ""),
            ]
        )
        text_tokens = _tokens(text)
        overlap = len(query_tokens & text_tokens)
        if overlap <= 0:
            continue
        score = float(overlap)
        if boost_exact and any(token in record_id.lower() or token in file.lower() for token in query_tokens):
            score += 2.0
        candidates.append(
            {
                "path": file,
                "record_id": record_id,
                "content_hash": record_id or file,
                "score": score,
                "metadata": {
                    "record_id": record_id,
                    "record_kind": str(record.get("kind") or ""),
                    "file": file,
                    "source_manifest_hash": "fixture-mh",
                },
            }
        )
    candidates.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return candidates


def _vector_candidates(*, embedding_records: list[dict], query: str, tmp_dir: Path) -> list[dict]:
    provider = FakeEmbeddingProvider(model_version="fake-benchmark-v1", dimensions=8)
    vector_store = CodeCompassVectorStore(index_path=tmp_dir / "vector-index.json")
    loader_payload = load_codecompass_embedding_documents(
        records=[
            {**row, "_provenance": {"output_kind": "embedding", "record_id": row.get("id")}}
            for row in embedding_records
        ],
        manifest={"manifest_hash": "fixture-mh", "profile_name": "java_spring_xml", "source_scope": "repo"},
    )
    vector_store.rebuild(
        documents=list(loader_payload["documents"]),
        embedding_provider=provider,
        retrieval_cache_state="benchmark-cache-v1",
        manifest_hash="fixture-mh",
    )
    engine = CodeCompassVectorEngine(store=vector_store, embedding_provider=provider)
    chunks = engine.search(query=query, top_k=8, retrieval_intent="fuzzy_semantic")
    return [
        {
            "path": str(chunk.get("source") or ""),
            "record_id": str((chunk.get("metadata") or {}).get("record_id") or ""),
            "content_hash": str((chunk.get("metadata") or {}).get("record_id") or ""),
            "score": float(chunk.get("score") or 0.0),
            "metadata": dict(chunk.get("metadata") or {}),
        }
        for chunk in chunks
    ]


def _graph_expansion_payload(*, graph_nodes: list[dict], graph_edges: list[dict], seed_ids: list[str], tmp_dir: Path) -> dict:
    graph_store = CodeCompassGraphStore(index_path=tmp_dir / "graph-index.json")
    graph_store.rebuild_from_output_records(
        records=[
            *[{**row, "_provenance": {"output_kind": "graph_nodes"}} for row in graph_nodes],
            *[{**row, "_provenance": {"output_kind": "graph_edges"}} for row in graph_edges],
        ],
        manifest_hash="fixture-mh",
    )
    expansion = expand_codecompass_graph(
        store=graph_store,
        seed_node_ids=seed_ids,
        profile="architecture_review",
    )
    return {"chunks": build_graph_context_chunks(expansion=expansion, max_content_chars=220)}


def _mode_metrics(payload: dict, expected_ids: set[str]) -> dict[str, float]:
    provenance = list(payload.get("provenance") or [])
    selected_ids = [str(item.get("record_id") or "") for item in provenance]
    hits = [idx for idx, record_id in enumerate(selected_ids, start=1) if record_id in expected_ids]
    recall = (len({record_id for record_id in selected_ids if record_id in expected_ids}) / float(len(expected_ids))) if expected_ids else 1.0
    mrr = (1.0 / float(hits[0])) if hits else 0.0
    selected = list(payload.get("selected") or [])
    explanation_coverage = (
        sum(1 for item in selected if dict(item.get("channel_contributions") or {})) / float(len(selected))
        if selected
        else 0.0
    )
    token_count = sum(max(1, len(str(item.get("file") or "")) // 4) for item in provenance)
    return {
        "recall_at_k": round(float(recall), 4),
        "mrr": round(float(mrr), 4),
        "selected_token_count": float(token_count),
        "explanation_coverage": round(float(explanation_coverage), 4),
    }


def test_codecompass_hybrid_retrieval_benchmark_fixture_modes_are_machine_readable(tmp_path):
    fixture_root = Path(__file__).resolve().parent / "fixtures" / "java_spring_xml"
    manifest = json.loads((fixture_root / "fixture_manifest.json").read_text(encoding="utf-8"))
    generated = dict(manifest["generated_outputs"])
    index_records = _read_jsonl(fixture_root / generated["index"])
    details_records = _read_jsonl(fixture_root / generated["details"])
    embedding_records = _read_jsonl(fixture_root / generated["embedding"])
    graph_nodes = _read_jsonl(fixture_root / generated["graph_nodes"])
    graph_edges = _read_jsonl(fixture_root / generated["graph_edges"])
    service = HybridRetrievalService()
    per_mode: dict[str, list[dict]] = {
        "baseline_lexical": [],
        "fts_only": [],
        "vector_only": [],
        "fts_vector": [],
        "graph_expanded": [],
        "full_hybrid": [],
    }
    for scenario in list(manifest["queries"]):
        query = str(scenario["query"])
        expected = {str(item) for item in list(scenario["expected_record_ids"])}
        lexical = _lexical_candidates([*index_records, *details_records], query, boost_exact=False)
        fts = _lexical_candidates([*index_records, *details_records], query, boost_exact=True)
        vector = _vector_candidates(
            embedding_records=embedding_records,
            query=query,
            tmp_dir=tmp_path / f"vector-{scenario['id']}",
        )
        seed_ids = [str(item.get("record_id") or "") for item in fts[:3] if str(item.get("record_id") or "")]
        graph_payload = _graph_expansion_payload(
            graph_nodes=graph_nodes,
            graph_edges=graph_edges,
            seed_ids=seed_ids,
            tmp_dir=tmp_path / f"graph-{scenario['id']}",
        )
        modes = {
            "baseline_lexical": {
                "contract": {"channels": ["lexical"], "fallback_order": ["lexical"]},
                "channels": {"lexical": lexical},
                "graph_expansion": None,
            },
            "fts_only": {
                "contract": {"channels": ["codecompass_fts"], "fallback_order": ["codecompass_fts"]},
                "channels": {"codecompass_fts": fts},
                "graph_expansion": None,
            },
            "vector_only": {
                "contract": {"channels": ["codecompass_vector"], "fallback_order": ["codecompass_vector"]},
                "channels": {"codecompass_vector": vector},
                "graph_expansion": None,
            },
            "fts_vector": {
                "contract": {"channels": ["codecompass_fts", "codecompass_vector"], "fallback_order": ["codecompass_fts", "codecompass_vector"]},
                "channels": {"codecompass_fts": fts, "codecompass_vector": vector},
                "graph_expansion": None,
            },
            "graph_expanded": {
                "contract": {"channels": ["codecompass_fts", "codecompass_graph"], "fallback_order": ["codecompass_fts", "codecompass_graph"]},
                "channels": {"codecompass_fts": fts, "codecompass_graph": []},
                "graph_expansion": graph_payload,
            },
            "full_hybrid": {
                "contract": {
                    "channels": ["codecompass_fts", "codecompass_vector", "codecompass_graph"],
                    "fallback_order": ["codecompass_fts", "codecompass_vector", "codecompass_graph"],
                },
                "channels": {"codecompass_fts": fts, "codecompass_vector": vector, "codecompass_graph": []},
                "graph_expansion": graph_payload,
            },
        }
        for mode_name, mode in modes.items():
            started = time.perf_counter()
            payload = service.retrieve(
                query=query,
                pipeline_contract=mode["contract"],
                channel_results=mode["channels"],
                graph_expansion=mode["graph_expansion"],
                channel_config={"codecompass_fts": True, "codecompass_vector": True, "codecompass_graph": True},
                top_k=5,
                task_type=str(scenario.get("task_kind") or "bugfix"),
                profile="balanced",
            )
            latency_ms = int((time.perf_counter() - started) * 1000)
            metrics = _mode_metrics(payload, expected)
            per_mode[mode_name].append(
                {
                    **metrics,
                    "latency_ms": float(latency_ms),
                    "scenario_id": str(scenario["id"]),
                    "final_chunk_count": float(len(list(payload.get("selected") or []))),
                }
            )

    benchmark = {
        "schema": "codecompass_hybrid_retrieval_benchmark.v1",
        "scenario_count": len(list(manifest["queries"])),
        "modes": {
            mode: {
                "recall_at_k": round(sum(item["recall_at_k"] for item in rows) / len(rows), 4),
                "mrr": round(sum(item["mrr"] for item in rows) / len(rows), 4),
                "selected_token_count": round(sum(item["selected_token_count"] for item in rows) / len(rows), 2),
                "latency_ms": round(sum(item["latency_ms"] for item in rows) / len(rows), 2),
                "explanation_coverage": round(sum(item["explanation_coverage"] for item in rows) / len(rows), 4),
            }
            for mode, rows in per_mode.items()
        },
    }
    assert benchmark["schema"] == "codecompass_hybrid_retrieval_benchmark.v1"
    assert benchmark["scenario_count"] >= 4
    assert set(benchmark["modes"]) == {"baseline_lexical", "fts_only", "vector_only", "fts_vector", "graph_expanded", "full_hybrid"}
    for mode_result in benchmark["modes"].values():
        assert 0.0 <= mode_result["recall_at_k"] <= 1.0
        assert 0.0 <= mode_result["mrr"] <= 1.0
        assert mode_result["selected_token_count"] >= 0.0
        assert mode_result["latency_ms"] >= 0.0
        assert 0.0 <= mode_result["explanation_coverage"] <= 1.0

