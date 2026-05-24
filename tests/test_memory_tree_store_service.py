"""OHA-008/009: Tests für MemoryTreeStoreService und MemoryTreeIngestionService."""
import json
import tempfile
from pathlib import Path

import pytest

from agent.services.memory_tree_store_service import (
    MemoryTreeStoreService,
    chunk_id,
    node_id,
)
from agent.services.memory_tree_ingestion_service import (
    IngestionStats,
    MemoryTreeIngestionService,
    _content_from_record,
    _label_from_record,
    _sensitivity_from_record,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    """In-memory SQLite store for tests — fully isolated per test."""
    from sqlmodel import SQLModel, create_engine
    from agent import db_models  # noqa: F401 — registers all tables
    import agent.database as db_module
    import agent.services.memory_tree_store_service as store_module

    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(test_engine)

    original = db_module.engine
    db_module.engine = test_engine
    # Also reset the module-level reference used by the service
    original_mod = store_module._db_module.engine
    store_module._db_module.engine = test_engine

    svc = MemoryTreeStoreService()
    yield svc

    db_module.engine = original
    store_module._db_module.engine = original_mod


@pytest.fixture
def ingestion(store):
    return MemoryTreeIngestionService(store=store)


@pytest.fixture
def ki_dir(tmp_path):
    """Minimal KnowledgeIndex output directory."""
    records_index = [
        {"record_id": "r1", "name": "OrderService", "file": "OrderService.java", "content": "Handles orders", "kind": "code"},
        {"record_id": "r2", "name": "PaymentService", "file": "PaymentService.java", "content": "Processes payments"},
    ]
    records_details = [
        {"record_id": "r3", "title": "OrderService docs", "content": "Detailed docs for order service"},
    ]
    records_relations = [
        {"record_id": "r4", "relation": "uses", "name": "OrderService->PaymentService", "content": "injects dependency"},
    ]

    (tmp_path / "index.jsonl").write_text("\n".join(json.dumps(r) for r in records_index))
    (tmp_path / "details.jsonl").write_text("\n".join(json.dumps(r) for r in records_details))
    (tmp_path / "relations.jsonl").write_text("\n".join(json.dumps(r) for r in records_relations))
    return tmp_path


# ---------------------------------------------------------------------------
# chunk_id / node_id determinism
# ---------------------------------------------------------------------------

def test_chunk_id_is_deterministic():
    id1 = chunk_id("src-1", "OrderService", "content text")
    id2 = chunk_id("src-1", "OrderService", "content text")
    assert id1 == id2


def test_chunk_id_is_32_hex_chars():
    cid = chunk_id("src-1", "label", "content")
    assert len(cid) == 32
    assert all(c in "0123456789abcdef" for c in cid)


def test_chunk_id_differs_on_content_change():
    id1 = chunk_id("src-1", "label", "content A")
    id2 = chunk_id("src-1", "label", "content B")
    assert id1 != id2


def test_node_id_is_deterministic():
    n1 = node_id("source", "my-label")
    n2 = node_id("source", "my-label")
    assert n1 == n2


def test_node_id_differs_on_type_change():
    assert node_id("source", "lbl") != node_id("topic", "lbl")


# ---------------------------------------------------------------------------
# MemoryTreeStoreService — ingest_chunk
# ---------------------------------------------------------------------------

def test_ingest_chunk_created(store):
    chunk, created = store.ingest_chunk(
        source_id="ki-001",
        source_type="code",
        label="OrderService",
        content="Handles orders",
    )
    assert created is True
    assert chunk.id is not None
    assert chunk.lifecycle == "admitted"


def test_ingest_chunk_idempotent(store):
    store.ingest_chunk(source_id="ki-001", source_type="code", label="X", content="content")
    _, created2 = store.ingest_chunk(source_id="ki-001", source_type="code", label="X", content="content")
    assert created2 is False


def test_ingest_chunk_different_content_creates_new(store):
    _, c1 = store.ingest_chunk(source_id="ki-001", source_type="code", label="X", content="A")
    _, c2 = store.ingest_chunk(source_id="ki-001", source_type="code", label="X", content="B")
    assert c1 is True
    assert c2 is True


def test_get_chunks_by_source(store):
    store.ingest_chunk(source_id="ki-001", source_type="code", label="A", content="a")
    store.ingest_chunk(source_id="ki-001", source_type="code", label="B", content="b")
    store.ingest_chunk(source_id="ki-999", source_type="code", label="C", content="c")
    chunks = store.get_chunks_by_source("ki-001")
    assert len(chunks) == 2


def test_count_chunks(store):
    store.ingest_chunk(source_id="ki-x", source_type="code", label="A", content="a")
    store.ingest_chunk(source_id="ki-x", source_type="code", label="B", content="b")
    assert store.count_chunks("ki-x") == 2
    assert store.count_chunks("ki-y") == 0


# ---------------------------------------------------------------------------
# MemoryTreeStoreService — lifecycle
# ---------------------------------------------------------------------------

def test_update_lifecycle_to_sealed(store):
    chunk, _ = store.ingest_chunk(source_id="ki-001", source_type="code", label="A", content="a")
    ok = store.update_lifecycle(chunk.id, "sealed")
    assert ok is True
    chunks = store.get_chunks_by_source("ki-001", lifecycle="sealed")
    assert len(chunks) == 1
    assert chunks[0].sealed_at is not None


def test_update_lifecycle_invalid(store):
    chunk, _ = store.ingest_chunk(source_id="ki-001", source_type="code", label="A", content="a")
    ok = store.update_lifecycle(chunk.id, "nonexistent_state")
    assert ok is False


def test_seal_source(store):
    for i in range(3):
        chunk, _ = store.ingest_chunk(source_id="ki-001", source_type="code", label=f"L{i}", content=f"content {i}")
        store.update_lifecycle(chunk.id, "buffered")
    count = store.seal_source("ki-001")
    assert count == 3
    assert store.count_chunks("ki-001", lifecycle="sealed") == 3


# ---------------------------------------------------------------------------
# MemoryTreeStoreService — nodes
# ---------------------------------------------------------------------------

def test_upsert_node_create(store):
    node = store.upsert_node(node_type="source", label="ki-001-source")
    assert node.id is not None
    assert node.node_type == "source"


def test_upsert_node_idempotent(store):
    n1 = store.upsert_node(node_type="source", label="ki-001-source")
    n2 = store.upsert_node(node_type="source", label="ki-001-source")
    assert n1.id == n2.id


def test_upsert_node_accumulates_provenance_refs(store):
    store.upsert_node(node_type="source", label="X", provenance_refs=["ref-a"])
    node = store.upsert_node(node_type="source", label="X", provenance_refs=["ref-b"])
    assert "ref-a" in node.provenance_refs
    assert "ref-b" in node.provenance_refs


def test_get_node(store):
    store.upsert_node(node_type="topic", label="OrderService")
    node = store.get_node("topic", "OrderService")
    assert node is not None
    assert node.label == "OrderService"


# ---------------------------------------------------------------------------
# MemoryTreeStoreService — jobs
# ---------------------------------------------------------------------------

def test_enqueue_job(store):
    job = store.enqueue_job(kind="ingest_source", payload={"source_id": "ki-001"})
    assert job.kind == "ingest_source"
    assert job.status == "pending"


def test_enqueue_job_dedupe(store):
    j1 = store.enqueue_job(kind="seal", payload={}, dedupe_key="seal:ki-001")
    j2 = store.enqueue_job(kind="seal", payload={}, dedupe_key="seal:ki-001")
    assert j1.id == j2.id


def test_complete_job(store):
    job = store.enqueue_job(kind="seal", payload={})
    ok = store.complete_job(job.id)
    assert ok is True


# ---------------------------------------------------------------------------
# Record parsing helpers
# ---------------------------------------------------------------------------

def test_sensitivity_from_record_known():
    assert _sensitivity_from_record({"sensitivity": "public"}) == "public"
    assert _sensitivity_from_record({"sensitivity": "secret"}) == "secret"


def test_sensitivity_from_record_unknown_defaults_to_internal():
    assert _sensitivity_from_record({"sensitivity": "weirdvalue"}) == "internal"
    assert _sensitivity_from_record({}) == "internal"


def test_label_from_record_prefers_name():
    assert _label_from_record({"name": "OrderService", "id": "r1"}) == "OrderService"


def test_label_from_record_fallback():
    assert _label_from_record({"record_id": "r99"}) == "r99"


def test_content_from_record():
    c = _content_from_record({"content": "hello", "file": "foo.java"})
    assert "hello" in c


# ---------------------------------------------------------------------------
# MemoryTreeIngestionService — KnowledgeIndex ingest
# ---------------------------------------------------------------------------

def test_ingest_knowledge_index_creates_chunks(ingestion, ki_dir):
    stats = ingestion.ingest_knowledge_index(
        knowledge_index_id="ki-001",
        output_dir=ki_dir,
        enabled=True,
    )
    assert stats.created >= 3  # at least 3 records with content
    assert stats.skipped_duplicate == 0
    assert stats.errors == 0


def test_ingest_knowledge_index_idempotent(ingestion, ki_dir):
    s1 = ingestion.ingest_knowledge_index(knowledge_index_id="ki-001", output_dir=ki_dir)
    s2 = ingestion.ingest_knowledge_index(knowledge_index_id="ki-001", output_dir=ki_dir)
    assert s2.created == 0
    assert s2.skipped_duplicate == s1.created


def test_ingest_knowledge_index_disabled(ingestion, ki_dir):
    stats = ingestion.ingest_knowledge_index(
        knowledge_index_id="ki-001",
        output_dir=ki_dir,
        enabled=False,
    )
    assert stats.created == 0
    assert stats.total_processed == 0


def test_ingest_knowledge_index_source_types(ingestion, ki_dir):
    stats = ingestion.ingest_knowledge_index(knowledge_index_id="ki-001", output_dir=ki_dir)
    assert "code" in stats.source_types
    assert "doc" in stats.source_types
    assert "relation" in stats.source_types


def test_ingest_knowledge_index_missing_dir(ingestion, tmp_path):
    stats = ingestion.ingest_knowledge_index(
        knowledge_index_id="ki-001",
        output_dir=tmp_path / "nonexistent",
    )
    assert stats.created == 0


def test_ingest_sensitivity_ceiling(ingestion, tmp_path):
    records = [
        {"record_id": "r1", "name": "A", "content": "public stuff", "sensitivity": "public"},
        {"record_id": "r2", "name": "B", "content": "secret stuff", "sensitivity": "secret"},
    ]
    (tmp_path / "index.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    stats = ingestion.ingest_knowledge_index(
        knowledge_index_id="ki-sec",
        output_dir=tmp_path,
        sensitivity_ceiling="internal",
    )
    assert stats.created == 1  # only the public record
    assert stats.skipped_policy == 1


# ---------------------------------------------------------------------------
# MemoryTreeIngestionService — CodeCompass graph ingest
# ---------------------------------------------------------------------------

def test_ingest_codecompass_graph_nodes(ingestion):
    artifact = {
        "nodes": [
            {"node_id": "n1", "node_type": "java_type", "attributes": {"name": "OrderService", "content": "handles orders", "file": "OrderService.java"}},
            {"node_id": "n2", "node_type": "java_method", "attributes": {"name": "placeOrder", "content": "places order", "file": "OrderService.java"}},
        ],
        "edges": [],
    }
    stats = ingestion.ingest_codecompass_graph(knowledge_index_id="ki-cc", graph_artifact=artifact)
    assert stats.created == 2
    assert stats.source_types.get("graph_node") == 2


def test_ingest_codecompass_graph_edges(ingestion):
    artifact = {
        "nodes": [],
        "edges": [
            {"source_id": "n1", "target_id": "n2", "relation": "calls_probable_target", "attributes": {"confidence": 0.9}},
        ],
    }
    stats = ingestion.ingest_codecompass_graph(knowledge_index_id="ki-cc2", graph_artifact=artifact)
    assert stats.created == 1
    assert stats.source_types.get("graph_edge") == 1


def test_ingest_codecompass_graph_idempotent(ingestion):
    artifact = {"nodes": [{"node_id": "n1", "node_type": "java_type", "attributes": {"name": "X", "content": "y"}}], "edges": []}
    ingestion.ingest_codecompass_graph(knowledge_index_id="ki-cc3", graph_artifact=artifact)
    s2 = ingestion.ingest_codecompass_graph(knowledge_index_id="ki-cc3", graph_artifact=artifact)
    assert s2.created == 0
    assert s2.skipped_duplicate == 1


def test_ingest_codecompass_graph_disabled(ingestion):
    artifact = {"nodes": [{"node_id": "n1", "node_type": "java_type", "attributes": {}}], "edges": []}
    stats = ingestion.ingest_codecompass_graph(knowledge_index_id="ki-cc4", graph_artifact=artifact, enabled=False)
    assert stats.created == 0
