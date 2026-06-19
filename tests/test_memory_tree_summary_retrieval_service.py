"""OHA-010/011: Tests for MemoryTreeSummaryService and MemoryTreeRetrievalService."""
import pytest
from sqlmodel import SQLModel, create_engine


# ---------------------------------------------------------------------------
# Fixtures — isolated in-memory SQLite per test
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    from agent import db_models  # noqa: F401
    import agent.database as db_module
    import agent.services.memory_tree_store_service as store_module
    from agent.services.memory_tree_store_service import MemoryTreeStoreService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    # Use monkeypatch so the engine swap is always restored, even if a
    # test body replaces the engine again or a sibling fixture rebinds
    # the attribute. Direct attribute writes here have caused cross-file
    # regressions in test_tasks_autopilot under the full pytest run.
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(store_module._db_module, "engine", engine)

    svc = MemoryTreeStoreService()
    yield svc


@pytest.fixture
def summary_svc(store):
    from agent.services.memory_tree_summary_service import MemoryTreeSummaryService
    return MemoryTreeSummaryService(store=store, llm_enabled=False)


@pytest.fixture
def retrieval_svc(store):
    from agent.services.memory_tree_retrieval_service import MemoryTreeRetrievalService
    return MemoryTreeRetrievalService(store=store, sensitivity_ceiling="internal_high")


@pytest.fixture
def populated_store(store):
    """Store with 3 sealed chunks for source ki-001 and 2 admitted for ki-002."""
    for i in range(3):
        chunk, _ = store.ingest_chunk(
            source_id="ki-001", source_type="code",
            label=f"OrderService_{i}", content=f"handles orders part {i}",
            sensitivity="internal",
        )
        store.update_lifecycle(chunk.id, "buffered")
        store.update_lifecycle(chunk.id, "sealed")

    for i in range(2):
        store.ingest_chunk(
            source_id="ki-002", source_type="doc",
            label=f"PaymentDocs_{i}", content=f"payment documentation {i}",
            sensitivity="public",
        )
    return store


# ---------------------------------------------------------------------------
# MemoryTreeSummaryService — source summaries
# ---------------------------------------------------------------------------

def test_build_source_summary_creates_node(summary_svc, populated_store):
    result = summary_svc.build_source_summary("ki-001")
    assert result.created is True
    assert result.leaf_count == 3
    assert result.scope == "source"
    assert result.summary_method == "deterministic"
    assert result.error is None


def test_build_source_summary_node_stored(summary_svc, populated_store):
    result = summary_svc.build_source_summary("ki-001")
    node = populated_store.get_node("source", "source:ki-001")
    assert node is not None
    assert node.label == "source:ki-001"
    assert len(node.child_chunk_ids) == 3
    assert "ki-001" in node.provenance_refs


def test_build_source_summary_no_chunks_returns_error(summary_svc, store):
    result = summary_svc.build_source_summary("nonexistent-source")
    assert result.created is False
    assert result.error == "no_chunks"
    assert result.leaf_count == 0


def test_build_source_summary_summary_text_contains_source_id(summary_svc, populated_store):
    result = summary_svc.build_source_summary("ki-001")
    node = populated_store.get_node("source", "source:ki-001")
    assert "ki-001" in node.summary
    assert "3" in node.summary


def test_build_source_summary_elapsed_measured(summary_svc, populated_store):
    result = summary_svc.build_source_summary("ki-001")
    assert result.elapsed_s >= 0.0


def test_build_source_summary_fallback_to_all_lifecycles(summary_svc, store):
    # Only admitted chunks — no sealed ones
    store.ingest_chunk(
        source_id="ki-adm", source_type="code",
        label="SomeClass", content="some content",
    )
    result = summary_svc.build_source_summary("ki-adm", lifecycle_filter="sealed")
    # Falls back to any lifecycle
    assert result.leaf_count == 1
    assert result.created is True


# ---------------------------------------------------------------------------
# MemoryTreeSummaryService — topic summaries
# ---------------------------------------------------------------------------

def test_build_topic_summary_creates_node(summary_svc, populated_store):
    result = summary_svc.build_topic_summary(
        "OrderManagement", source_ids=["ki-001"], hotness=1.0
    )
    assert result.created is True
    assert result.scope == "topic"
    assert result.leaf_count == 3


def test_build_topic_summary_node_has_provenance(summary_svc, populated_store):
    summary_svc.build_topic_summary("OrderManagement", source_ids=["ki-001", "ki-002"])
    node = populated_store.get_node("topic", "topic:OrderManagement")
    assert node is not None
    assert "ki-001" in node.provenance_refs
    assert "ki-002" in node.provenance_refs


def test_build_topic_summary_cold_topic_skipped(summary_svc, store):
    result = summary_svc.build_topic_summary(
        "ColdTopic", source_ids=[], hotness=0.3
    )
    assert result.created is False
    assert result.meta.get("reason") == "cold_topic"


def test_build_topic_summary_summary_text_has_topic(summary_svc, populated_store):
    summary_svc.build_topic_summary("OrderManagement", source_ids=["ki-001"])
    node = populated_store.get_node("topic", "topic:OrderManagement")
    assert "OrderManagement" in node.summary


# ---------------------------------------------------------------------------
# MemoryTreeSummaryService — global digest
# ---------------------------------------------------------------------------

def test_build_global_digest_creates_node(summary_svc, populated_store):
    result = summary_svc.build_global_digest(
        "project-daily-2026",
        topic_labels=["OrderManagement"],
        source_ids=["ki-001"],
    )
    assert result.created is True
    assert result.scope == "global"


def test_build_global_digest_leaf_count(summary_svc, populated_store):
    result = summary_svc.build_global_digest(
        "digest-scope", source_ids=["ki-001", "ki-002"]
    )
    assert result.leaf_count == 5  # 3 + 2


def test_build_global_digest_node_stored(summary_svc, populated_store):
    summary_svc.build_global_digest("scope1", source_ids=["ki-001"])
    node = populated_store.get_node("global", "global:scope1")
    assert node is not None
    assert "ki-001" in node.provenance_refs


# ---------------------------------------------------------------------------
# MemoryTreeSummaryService — seal and summarise
# ---------------------------------------------------------------------------

def test_seal_and_summarise_source(summary_svc, store):
    for i in range(4):
        chunk, _ = store.ingest_chunk(
            source_id="ki-s", source_type="code",
            label=f"L{i}", content=f"content {i}",
        )
        store.update_lifecycle(chunk.id, "buffered")
    result = summary_svc.seal_and_summarise_source("ki-s")
    assert result.leaf_count == 4
    assert result.created is True
    assert store.count_chunks("ki-s", lifecycle="sealed") == 4


# ---------------------------------------------------------------------------
# MemoryTreeRetrievalService — source scope
# ---------------------------------------------------------------------------

def test_retrieve_source_returns_chunks(retrieval_svc, populated_store):
    result = retrieval_svc.retrieve_source("ki-001")
    assert result.total_chunks == 3
    assert result.scope == "source"


def test_retrieve_source_with_query_filters(retrieval_svc, populated_store):
    result = retrieval_svc.retrieve_source("ki-001", query="orders")
    assert result.total_chunks > 0
    for c in result.chunks:
        assert "order" in (c.label + c.content).lower()


def test_retrieve_source_includes_summary_node(retrieval_svc, summary_svc, populated_store):
    summary_svc.build_source_summary("ki-001")
    result = retrieval_svc.retrieve_source("ki-001", with_summary=True)
    assert result.summary_node is not None
    assert result.summary_node.node_type == "source"


def test_retrieve_source_no_summary_when_disabled(retrieval_svc, populated_store):
    result = retrieval_svc.retrieve_source("ki-001", with_summary=False)
    assert result.summary_node is None


def test_retrieve_source_sensitivity_filter(retrieval_svc, store):
    store.ingest_chunk(
        source_id="sec-src", source_type="code",
        label="SecretClass", content="very sensitive",
        sensitivity="secret",
    )
    store.ingest_chunk(
        source_id="sec-src", source_type="code",
        label="PublicClass", content="safe content",
        sensitivity="public",
    )
    result = retrieval_svc.retrieve_source(
        "sec-src", lifecycle=None, sensitivity_ceiling="internal"
    )
    assert result.total_chunks == 1
    assert result.filtered_by_policy == 1
    assert result.chunks[0].label == "PublicClass"


def test_retrieve_source_limit_respected(retrieval_svc, store):
    for i in range(20):
        store.ingest_chunk(
            source_id="big-src", source_type="code",
            label=f"Class{i}", content=f"content {i}",
        )
    result = retrieval_svc.retrieve_source("big-src", lifecycle=None, limit=5)
    assert result.total_chunks == 5


# ---------------------------------------------------------------------------
# MemoryTreeRetrievalService — topic scope
# ---------------------------------------------------------------------------

def test_retrieve_topic_returns_chunks(retrieval_svc, summary_svc, populated_store):
    summary_svc.build_topic_summary("OrderMgmt", source_ids=["ki-001"])
    result = retrieval_svc.retrieve_topic("OrderMgmt")
    assert result.total_chunks > 0
    assert result.scope == "topic"


def test_retrieve_topic_not_found(retrieval_svc, store):
    result = retrieval_svc.retrieve_topic("NonExistentTopic")
    assert result.total_chunks == 0
    assert result.meta.get("reason") == "topic_node_not_found"


def test_retrieve_topic_with_summary_node(retrieval_svc, summary_svc, populated_store):
    summary_svc.build_topic_summary("PM", source_ids=["ki-002"])
    result = retrieval_svc.retrieve_topic("PM", with_summary=True)
    assert result.summary_node is not None


# ---------------------------------------------------------------------------
# MemoryTreeRetrievalService — global scope
# ---------------------------------------------------------------------------

def test_retrieve_global_not_found(retrieval_svc, store):
    result = retrieval_svc.retrieve_global("no-scope")
    assert result.total_chunks == 0
    assert result.meta.get("reason") == "global_node_not_found"


def test_retrieve_global_returns_chunks(retrieval_svc, summary_svc, populated_store):
    summary_svc.build_global_digest("g-scope", source_ids=["ki-001"])
    result = retrieval_svc.retrieve_global("g-scope")
    assert result.total_chunks > 0
    assert result.scope == "global"


# ---------------------------------------------------------------------------
# MemoryTreeRetrievalService — cross-scope search
# ---------------------------------------------------------------------------

def test_search_by_source_id(retrieval_svc, populated_store):
    result = retrieval_svc.search("order", source_ids=["ki-001"])
    assert result.total_chunks > 0
    assert result.scope == "any"


def test_search_deduplicates_chunks(retrieval_svc, populated_store):
    # Retrieve same source twice via two mechanisms
    result = retrieval_svc.search(
        "order",
        source_ids=["ki-001", "ki-001"],
    )
    ids = [c.chunk_id for c in result.chunks]
    assert len(ids) == len(set(ids))


def test_search_combines_sources(retrieval_svc, populated_store):
    result = retrieval_svc.search("", source_ids=["ki-001", "ki-002"])
    assert result.total_chunks == 5


def test_search_global_scope(retrieval_svc, summary_svc, populated_store):
    summary_svc.build_global_digest("g2", source_ids=["ki-001"])
    result = retrieval_svc.search("order", global_scope="g2")
    assert result.total_chunks > 0


def test_search_no_results_for_missing_query(retrieval_svc, populated_store):
    result = retrieval_svc.search("zzz_no_match", source_ids=["ki-001"])
    assert result.total_chunks == 0
