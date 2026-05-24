"""OHA-013/014/015: Context Policy MemoryTree scopes and cloud-leak regression tests.

Verifies that:
- sensitive chunks (secret/credential/internal_high) are denied for external_cloud destinations
- public/internal chunks pass through correctly
- denied chunks never appear in context_text or memory_tree_view.chunks
- denied reasons are auditable
- local workers can access more than external_cloud workers
- ContextBundler memory_tree_view is correctly filtered
"""
import pytest
from unittest.mock import MagicMock

from agent.services.task_context_policy_service import (
    _build_destination,
    _intent_to_tree_scope,
    filter_chunks_for_destination,
)
from agent.services.context_bundle_service import ContextBundler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk(sensitivity: str, content: str = "data") -> dict:
    return {
        "id": f"chunk-{sensitivity}",
        "content": content,
        "metadata": {"sensitivity": sensitivity},
    }


def _make_mt_result(chunks: list[dict]) -> MagicMock:
    """Build a mock MemoryRetrievalResult from a list of chunk dicts."""
    result = MagicMock()
    result.scope = "source"
    result.query = "test query"
    result.filtered_by_policy = 0
    result.drilldown_refs = []
    result.summary_node = None

    mock_chunks = []
    for c in chunks:
        mc = MagicMock()
        mc.chunk_id = c["id"]
        mc.source_id = "src-1"
        mc.label = c.get("label", c["id"])
        mc.content = c["content"]
        mc.sensitivity = c["metadata"]["sensitivity"]
        mc.score = 1.0
        mock_chunks.append(mc)
    result.chunks = mock_chunks
    return result


# ---------------------------------------------------------------------------
# OHA-013: _build_destination
# ---------------------------------------------------------------------------

def test_build_destination_local_defaults():
    dest = _build_destination()
    assert dest["cloud_effective"] is False
    assert dest["external_effective"] is False
    assert dest["local_effective"] is True
    assert dest["runtime_kind"] == "local"


def test_build_destination_cloud():
    dest = _build_destination(
        worker_id="claude-remote",
        runtime_kind="cloud",
        provider_location="remote",
        model_scope="external_cloud_allowed",
        cloud_effective=True,
        external_effective=True,
    )
    assert dest["cloud_effective"] is True
    assert dest["local_effective"] is False


# ---------------------------------------------------------------------------
# OHA-013: _intent_to_tree_scope
# ---------------------------------------------------------------------------

def test_tree_scope_architecture_intent():
    assert _intent_to_tree_scope("architecture_and_decision_context") == "global"


def test_tree_scope_symbol_intent():
    assert _intent_to_tree_scope("symbol_and_dependency_neighborhood") == "topic"


def test_tree_scope_config_intent():
    assert _intent_to_tree_scope("configuration_contracts_and_runtime_edges") == "topic"


def test_tree_scope_default_is_source():
    assert _intent_to_tree_scope("localize_failure_and_fix") == "source"
    assert _intent_to_tree_scope("") == "source"


# ---------------------------------------------------------------------------
# OHA-013: filter_chunks_for_destination — local worker
# ---------------------------------------------------------------------------

def test_local_worker_allows_internal_high():
    local_dest = _build_destination()
    chunks = [_chunk("internal_high"), _chunk("internal"), _chunk("public")]
    result = filter_chunks_for_destination(chunks, local_dest, sensitivity_ceiling="internal_high")
    assert result["allowed_count"] == 3
    assert result["denied_count"] == 0


def test_local_worker_blocks_secret():
    local_dest = _build_destination()
    chunks = [_chunk("secret"), _chunk("credential"), _chunk("public")]
    result = filter_chunks_for_destination(chunks, local_dest, sensitivity_ceiling="internal_high")
    assert result["allowed_count"] == 1
    assert result["denied_count"] == 2
    assert any("ceiling_exceeded" in r for r in result["denied_reasons"])


def test_local_worker_allows_more_than_cloud():
    local_dest = _build_destination()
    cloud_dest = _build_destination(cloud_effective=True, external_effective=True)
    chunks = [_chunk("internal_high"), _chunk("internal"), _chunk("public")]

    local_result = filter_chunks_for_destination(chunks, local_dest, sensitivity_ceiling="internal_high")
    cloud_result = filter_chunks_for_destination(chunks, cloud_dest, sensitivity_ceiling="internal_high")

    assert local_result["allowed_count"] > cloud_result["allowed_count"]


# ---------------------------------------------------------------------------
# OHA-015: cloud-leak regression — external_cloud destination
# ---------------------------------------------------------------------------

def test_cloud_dest_denies_secret():
    cloud_dest = _build_destination(cloud_effective=True)
    chunks = [_chunk("secret", "TOP SECRET data")]
    result = filter_chunks_for_destination(chunks, cloud_dest)
    assert result["allowed_count"] == 0
    assert result["denied_count"] == 1
    assert any("cloud_deny" in r for r in result["denied_reasons"])


def test_cloud_dest_denies_credential():
    cloud_dest = _build_destination(cloud_effective=True)
    chunks = [_chunk("credential", "API_KEY=...")]
    result = filter_chunks_for_destination(chunks, cloud_dest)
    assert result["allowed_count"] == 0
    assert result["denied_count"] == 1


def test_cloud_dest_denies_security_sensitive():
    cloud_dest = _build_destination(cloud_effective=True)
    chunks = [_chunk("security_sensitive", "auth tokens here")]
    result = filter_chunks_for_destination(chunks, cloud_dest)
    assert result["allowed_count"] == 0


def test_cloud_dest_denies_internal_high():
    cloud_dest = _build_destination(cloud_effective=True)
    chunks = [_chunk("internal_high", "internal architecture")]
    result = filter_chunks_for_destination(chunks, cloud_dest)
    assert result["allowed_count"] == 0
    assert result["denied_count"] == 1


def test_cloud_dest_allows_public():
    cloud_dest = _build_destination(cloud_effective=True)
    chunks = [_chunk("public", "open source docs")]
    result = filter_chunks_for_destination(chunks, cloud_dest)
    assert result["allowed_count"] == 1
    assert result["denied_count"] == 0


def test_cloud_dest_allows_internal():
    cloud_dest = _build_destination(cloud_effective=True)
    chunks = [_chunk("internal", "standard internal info")]
    result = filter_chunks_for_destination(chunks, cloud_dest)
    assert result["allowed_count"] == 1


def test_mixed_chunks_cloud_regression():
    """Regression: mixed sensitivity batch — secret never leaks to cloud."""
    cloud_dest = _build_destination(cloud_effective=True)
    chunks = [
        _chunk("public", "safe"),
        _chunk("internal", "ok"),
        _chunk("internal_high", "sensitive"),
        _chunk("secret", "SECRET"),
        _chunk("credential", "password=hunter2"),
    ]
    result = filter_chunks_for_destination(chunks, cloud_dest)
    allowed_ids = {c["id"] for c in result["allowed_chunks"]}
    assert "chunk-secret" not in allowed_ids
    assert "chunk-credential" not in allowed_ids
    assert "chunk-internal_high" not in allowed_ids
    assert "chunk-public" in allowed_ids
    assert "chunk-internal" in allowed_ids
    assert result["denied_count"] == 3


def test_denied_reasons_are_auditable():
    cloud_dest = _build_destination(cloud_effective=True)
    chunks = [_chunk("secret"), _chunk("credential")]
    result = filter_chunks_for_destination(chunks, cloud_dest)
    assert len(result["denied_reasons"]) > 0
    for reason in result["denied_reasons"]:
        assert isinstance(reason, str)
        assert len(reason) > 0


# ---------------------------------------------------------------------------
# OHA-014: ContextBundler with memory_tree_view
# ---------------------------------------------------------------------------

def test_build_bundle_without_memory_tree_view():
    bundle = ContextBundler.build_bundle(
        query="test",
        context_payload={"chunks": [_chunk("public")], "context_text": "hello"},
        llm_scope="local_only",
    )
    assert bundle["schema"] == "worker_context_bundle.v1"
    assert "memory_tree_view" not in bundle
    assert bundle["policy_filter"]["memory_tree_denied_count"] == 0


def test_build_bundle_with_memory_tree_view_local():
    mt_result = _make_mt_result([
        {"id": "mt-1", "content": "MemTree chunk 1", "metadata": {"sensitivity": "internal"}},
        {"id": "mt-2", "content": "MemTree chunk 2", "metadata": {"sensitivity": "public"}},
    ])
    bundle = ContextBundler.build_bundle(
        query="q",
        context_payload={"chunks": [], "context_text": None},
        llm_scope="local_only",
        memory_tree_retrieval_result=mt_result,
    )
    assert "memory_tree_view" in bundle
    assert bundle["memory_tree_view"]["chunk_count"] == 2
    assert bundle["memory_tree_view"]["denied_count"] == 0


def test_build_bundle_memory_tree_view_filters_secret_for_cloud():
    """Cloud-leak: secret MemoryTree chunks must not appear in bundle for cloud scope."""
    mt_result = _make_mt_result([
        {"id": "mt-secret", "content": "top secret", "metadata": {"sensitivity": "secret"}},
        {"id": "mt-public", "content": "public info", "metadata": {"sensitivity": "public"}},
    ])
    bundle = ContextBundler.build_bundle(
        query="q",
        context_payload={"chunks": [], "context_text": None},
        llm_scope="external_cloud_allowed",
        memory_tree_retrieval_result=mt_result,
    )
    view = bundle["memory_tree_view"]
    chunk_ids = [c["chunk_id"] for c in view["chunks"]]
    assert "mt-secret" not in chunk_ids
    assert "mt-public" in chunk_ids
    assert view["denied_count"] >= 1


def test_build_bundle_memory_tree_view_with_summary_node():
    mt_result = _make_mt_result([
        {"id": "mt-1", "content": "data", "metadata": {"sensitivity": "public"}},
    ])
    summary_node = MagicMock()
    summary_node.node_id = "node-123"
    summary_node.node_type = "source"
    summary_node.label = "source:ki-001"
    summary_node.summary = "Source summary text"
    summary_node.leaf_count = 1
    mt_result.summary_node = summary_node

    bundle = ContextBundler.build_bundle(
        query="q",
        context_payload={"chunks": [], "context_text": None},
        memory_tree_retrieval_result=mt_result,
    )
    view = bundle["memory_tree_view"]
    assert view["summary_node"] is not None
    assert view["summary_node"]["node_id"] == "node-123"
    assert view["summary_node"]["summary"] == "Source summary text"


def test_build_bundle_policy_filter_counts_separate():
    """Standard chunks and MemoryTree chunks are counted separately in policy_filter."""
    mt_result = _make_mt_result([
        {"id": "mt-s", "content": "secret", "metadata": {"sensitivity": "secret"}},
    ])
    bundle = ContextBundler.build_bundle(
        query="q",
        context_payload={
            "chunks": [_chunk("internal_high")],
            "context_text": None,
        },
        llm_scope="external_cloud_allowed",
        memory_tree_retrieval_result=mt_result,
    )
    pf = bundle["policy_filter"]
    # Standard chunk (internal_high) denied
    assert pf["denied_count"] == 1
    # MemTree secret chunk denied separately
    assert pf["memory_tree_denied_count"] == 1
    assert pf["memory_tree_allowed_count"] == 0


def test_existing_bundle_tests_still_pass():
    """Backwards-compat: build_bundle without memory_tree_retrieval_result is identical."""
    chunks = [_chunk("public"), _chunk("internal")]
    bundle = ContextBundler.build_bundle(
        query="x",
        context_payload={"chunks": chunks, "context_text": "some text", "token_estimate": 100},
        policy_mode="standard",
        llm_scope="local_only",
    )
    assert bundle["chunk_count"] == 2
    assert bundle["context_text"] == "some text"
    assert bundle["token_estimate"] == 100
    assert bundle["policy_filter"]["denied_count"] == 0


# ---------------------------------------------------------------------------
# OHA-013: build_context_policy includes retrieval_tree_scope + destination
# ---------------------------------------------------------------------------

def test_build_context_policy_includes_tree_scope(monkeypatch):
    """build_context_policy now returns retrieval_tree_scope in context_policy."""
    from agent.services.task_context_policy_service import TaskContextPolicyService

    svc = TaskContextPolicyService()

    # Stub out the parts that need Flask app context / DB
    monkeypatch.setattr(svc, "resolve_context_bundle_policy", lambda: {})
    monkeypatch.setattr(svc, "derive_task_neighborhood", lambda **kw: {
        "depends_on_task_ids": [],
        "dependent_task_ids": [],
        "sibling_task_ids": [],
        "completed_neighbor_task_ids": [],
        "neighbor_task_ids": [],
    })

    parent_task = {"id": "t1", "goal_id": "g1"}
    ctx_policy, hints, _ = svc.build_context_policy(
        parent_task=parent_task,
        data=None,
        effective_task_kind="architecture",
    )
    assert "retrieval_tree_scope" in ctx_policy
    assert ctx_policy["retrieval_tree_scope"] == "global"


def test_build_context_policy_destination_cloud(monkeypatch):
    from agent.services.task_context_policy_service import TaskContextPolicyService

    svc = TaskContextPolicyService()
    monkeypatch.setattr(svc, "resolve_context_bundle_policy", lambda: {})
    monkeypatch.setattr(svc, "derive_task_neighborhood", lambda **kw: {
        "depends_on_task_ids": [], "dependent_task_ids": [],
        "sibling_task_ids": [], "completed_neighbor_task_ids": [], "neighbor_task_ids": [],
    })

    cloud_dest = _build_destination(cloud_effective=True, external_effective=True)
    ctx_policy, _, _ = svc.build_context_policy(
        parent_task={}, data=None, effective_task_kind="bugfix",
        destination=cloud_dest,
    )
    assert ctx_policy["destination"]["cloud_effective"] is True
    assert ctx_policy["retrieval_tree_scope"] == "source"
