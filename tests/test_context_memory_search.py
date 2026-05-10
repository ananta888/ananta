"""Tests for context_resolver.py (T026-T028), memory_stores.py (T029), session_search.py (T030)."""
import pytest

from worker.core.context_resolver import (
    ContextBlock,
    ContextCompressor,
    ContextResolver,
    ContextSensitivity,
    TokenBudget,
    _estimate_tokens,
)
from worker.core.memory_stores import MemoryStoreKind, WorkerMemoryStores
from worker.core.session_search import SearchTarget, SessionSearchIndex


# ── EW-T026: ContextResolver ──────────────────────────────────────────────────

class TestContextResolver:
    def setup_method(self):
        self.resolver = ContextResolver()

    def test_valid_refs_resolved(self):
        refs = [
            {"source_type": "task_description", "origin_id": "t1",
             "provenance": "hub", "content": "Do X", "priority": 0},
        ]
        blocks, errors = self.resolver.resolve(refs)
        assert len(blocks) == 1 and not errors

    def test_all_files_dump_rejected(self):
        refs = [{"source_type": "all_files", "origin_id": "repo", "content": "..."}]
        blocks, errors = self.resolver.resolve(refs)
        assert len(blocks) == 0
        assert any("context_unbounded_dump" in e for e in errors)

    def test_full_repo_dump_rejected(self):
        refs = [{"source_type": "full_repo_dump", "origin_id": "repo", "content": "..."}]
        _, errors = self.resolver.resolve(refs)
        assert any("context_unbounded_dump" in e for e in errors)

    def test_too_many_refs_rejected(self):
        refs = [
            {"source_type": "file_content", "origin_id": f"f{i}", "content": "x"}
            for i in range(60)
        ]
        blocks, errors = self.resolver.resolve(refs)
        assert len(blocks) == 0
        assert any("too many refs" in e for e in errors)

    def test_oversized_block_rejected(self):
        big_content = "x" * (16_000 * 4 + 100)
        refs = [{"source_type": "file_content", "origin_id": "big", "content": big_content}]
        _, errors = self.resolver.resolve(refs)
        assert any("context_budget_exceeded" in e for e in errors)

    def test_allowed_source_types_filter(self):
        refs = [
            {"source_type": "task_description", "origin_id": "t1", "content": "hello"},
            {"source_type": "file_content", "origin_id": "f1", "content": "world"},
        ]
        blocks, errors = self.resolver.resolve(refs, allowed_source_types=["task_description"])
        assert len(blocks) == 1
        assert any("context_sensitivity_blocked" in e for e in errors)

    def test_sensitivity_parsed_from_ref(self):
        refs = [{"source_type": "task_description", "origin_id": "t1",
                 "content": "secret thing", "sensitivity": "secret"}]
        blocks, _ = self.resolver.resolve(refs)
        assert blocks[0].sensitivity == ContextSensitivity.secret

    def test_priority_0_marks_p0(self):
        refs = [{"source_type": "task_description", "origin_id": "t1",
                 "content": "objective", "priority": 0}]
        blocks, _ = self.resolver.resolve(refs)
        assert blocks[0].is_p0


# ── EW-T027: TokenBudget ─────────────────────────────────────────────────────

class TestTokenBudget:
    def setup_method(self):
        self.budget = TokenBudget(global_limit=100)

    def test_blocks_within_budget_all_kept(self):
        blocks = [
            ContextBlock("task_description", "t1", "hub", token_estimate=30, content="A"),
            ContextBlock("file_content", "f1", "hub", token_estimate=30, content="B"),
        ]
        kept, dropped = self.budget.check(blocks)
        assert len(kept) == 2 and not dropped

    def test_blocks_over_budget_dropped(self):
        blocks = [
            ContextBlock("task_description", "t1", "hub", token_estimate=60, content="A"),
            ContextBlock("file_content", "f1", "hub", token_estimate=60, content="B"),
        ]
        kept, dropped = self.budget.check(blocks)
        assert len(kept) == 1
        assert len(dropped) == 1

    def test_p0_blocks_never_dropped(self):
        blocks = [
            ContextBlock("task_description", "t1", "hub",
                         token_estimate=90, content="objective", priority=0),
            ContextBlock("file_content", "f1", "hub",
                         token_estimate=90, content="big file", priority=50),
        ]
        kept, dropped = self.budget.check(blocks)
        kept_ids = {b.origin_id for b in kept}
        assert "t1" in kept_ids       # P0 never dropped
        assert len(dropped) >= 1      # f1 should have been dropped (strings)

    def test_per_source_limit_enforced(self):
        budget = TokenBudget(
            global_limit=1000,
            per_source_limits={"file_content": 50},
        )
        blocks = [
            ContextBlock("file_content", "f1", "hub", token_estimate=30, content="A"),
            ContextBlock("file_content", "f2", "hub", token_estimate=30, content="B"),
        ]
        kept, dropped = budget.check(blocks)
        # Total file_content would be 60 > 50 → second one dropped
        assert len(dropped) >= 1


# ── EW-T028: ContextCompressor ───────────────────────────────────────────────

class TestContextCompressor:
    def setup_method(self):
        self.compressor = ContextCompressor()

    def test_compression_reduces_tokens(self):
        blocks = [
            ContextBlock("task_description", "t1", "hub",
                         token_estimate=5000, content="A" * 20000),
        ]
        result = self.compressor.compress(blocks, max_tokens=100)
        assert result.compressed_tokens <= 200  # generous margin for overhead

    def test_source_hashes_included(self):
        blocks = [
            ContextBlock("task_description", "t1", "hub",
                         token_estimate=10, content="hello world"),
        ]
        result = self.compressor.compress(blocks)
        assert len(result.source_hashes) == 1

    def test_not_marked_as_raw_source(self):
        blocks = [ContextBlock("task_description", "t1", "hub", content="abc")]
        result = self.compressor.compress(blocks)
        assert result.is_raw_source is False

    def test_preserve_keywords_kept(self):
        content = (
            "Objective: build the feature\n"
            "Some random filler text that can be dropped\n"
            "Acceptance criteria: must pass all tests\n"
            "More filler that is not critical\n"
        )
        blocks = [ContextBlock("task_description", "t1", "hub",
                               token_estimate=50, content=content)]
        result = self.compressor.compress(blocks, max_tokens=30)
        # Preserved lines should appear in compressed output
        assert "Objective" in result.compressed_content or "criteria" in result.compressed_content.lower()

    def test_as_block_returns_context_block(self):
        blocks = [ContextBlock("task_description", "t1", "hub", content="hello")]
        result = self.compressor.compress(blocks)
        block = result.as_block(source_type="task_description", origin_id="t1-compressed")
        assert isinstance(block, ContextBlock)
        assert block.provenance == "compressed"


# ── EW-T029: WorkerMemoryStores ───────────────────────────────────────────────

class TestWorkerMemoryStores:
    def setup_method(self):
        self.stores = WorkerMemoryStores()

    def test_session_write_with_capability(self):
        result = self.stores.write(
            "worker_session_memory", "k", "v",
            task_id="t1", has_memory_write_capability=True)
        assert result.success
        assert result.reason_code == "memory_write_ok"

    def test_session_write_without_capability_denied(self):
        result = self.stores.write(
            "worker_session_memory", "k", "v",
            task_id="t1", has_memory_write_capability=False)
        assert not result.success
        assert result.reason_code == "memory_write_requires_approval"

    def test_project_write_with_capability(self):
        result = self.stores.write(
            "project_execution_memory", "project_key", "value",
            task_id="t1", has_memory_write_capability=True)
        assert result.success

    def test_long_term_write_becomes_proposal(self):
        result = self.stores.write(
            "proposed_long_term_memory", "fact", "the sky is blue",
            task_id="t1", has_memory_write_capability=True, hub_approved=False)
        assert result.success
        assert result.reason_code == "memory_write_proposal"
        assert result.entry.is_proposal is True

    def test_long_term_write_with_hub_approval_direct(self):
        result = self.stores.write(
            "proposed_long_term_memory", "fact", "approved fact",
            task_id="t1", has_memory_write_capability=True, hub_approved=True)
        assert result.success
        assert result.entry.approved is True
        assert result.entry.is_proposal is False

    def test_unknown_store_fails(self):
        result = self.stores.write(
            "nonexistent_store", "k", "v",
            task_id="t1", has_memory_write_capability=True)
        assert not result.success
        assert result.reason_code == "memory_store_not_found"

    def test_session_memory_discarded(self):
        self.stores.write("worker_session_memory", "k", "v",
                          task_id="t1", has_memory_write_capability=True)
        count = self.stores.discard_session()
        assert count == 1

    def test_read_after_write(self):
        self.stores.write("worker_session_memory", "mykey", "myval",
                          task_id="t1", has_memory_write_capability=True)
        entry = self.stores.session.read("mykey")
        assert entry is not None and entry.value == "myval"

    def test_search_session_memory(self):
        self.stores.write("worker_session_memory", "nginx_fix", "restart nginx",
                          task_id="t1", has_memory_write_capability=True)
        results = self.stores.session.search("nginx")
        assert len(results) >= 1

    def test_proposals_listed(self):
        self.stores.write("proposed_long_term_memory", "k1", "v1",
                          task_id="t1", has_memory_write_capability=True)
        self.stores.write("proposed_long_term_memory", "k2", "v2",
                          task_id="t1", has_memory_write_capability=True)
        proposals = self.stores.all_proposals()
        assert len(proposals) == 2

    def test_approve_proposal(self):
        self.stores.write("proposed_long_term_memory", "key", "val",
                          task_id="t1", has_memory_write_capability=True)
        ok = self.stores.long_term.approve_proposal("key")
        assert ok is True
        entry = self.stores.long_term.read("key")
        assert entry.approved is True and entry.is_proposal is False

    def test_empty_key_rejected(self):
        result = self.stores.write("worker_session_memory", "", "value",
                                   task_id="t1", has_memory_write_capability=True)
        assert not result.success


# ── EW-T030: SessionSearchIndex ──────────────────────────────────────────────

class TestSessionSearchIndex:
    def setup_method(self):
        self.index = SessionSearchIndex()
        self.index.index_task(task_id="t1", summary="fix nginx config", status="success")
        self.index.index_task(task_id="t2", summary="patch python file", status="failed")
        self.index.index_failure(task_id="t2", reason_code="patch_scope_violation",
                                  detail="outside workspace")
        self.index.index_artifact(artifact_id="a1", kind="patch", summary="fixed nginx.conf",
                                   provenance="t1:step1")
        self.index.index_decision(correlation_id="c1", decision="denied",
                                   reason_code="missing_capability", operation="shell_execute")

    def test_search_finds_task(self):
        results = self.index.search("nginx")
        ids = [r.id for r in results]
        assert "t1" in ids

    def test_search_finds_failure(self):
        results = self.index.search("patch_scope_violation")
        assert any(r.target_type == SearchTarget.failure for r in results)

    def test_search_finds_artifact(self):
        results = self.index.search("nginx.conf")
        assert any(r.target_type == SearchTarget.patch_artifact for r in results)

    def test_search_finds_decision(self):
        results = self.index.search("denied missing_capability")
        assert any(r.target_type == SearchTarget.decision for r in results)

    def test_snippet_bounded(self):
        results = self.index.search("nginx")
        for r in results:
            assert len(r.snippet) <= 350  # SNIPPET_MAX_CHARS + ellipsis overhead

    def test_target_type_filter(self):
        results = self.index.search("patch",
                                    target_types=[SearchTarget.patch_artifact])
        assert all(r.target_type == SearchTarget.patch_artifact for r in results)

    def test_project_scope_filter(self):
        index = SessionSearchIndex()
        index.index_task(task_id="proj-t1", summary="nginx fix", status="success",
                         project_id="proj-a")
        index.index_task(task_id="other-t1", summary="nginx fix", status="success",
                         project_id="proj-b")
        results = index.search("nginx", project_id="proj-a")
        assert all(r.project_id == "proj-a" for r in results if r.project_id)

    def test_score_between_0_and_1(self):
        results = self.index.search("nginx patch")
        for r in results:
            assert 0.0 <= r.score <= 1.0

    def test_empty_query_returns_nothing(self):
        results = self.index.search("")
        assert results == []

    def test_max_results_respected(self):
        index = SessionSearchIndex()
        for i in range(20):
            index.index_task(task_id=f"t{i}", summary="common keyword", status="success")
        results = index.search("common", max_results=5)
        assert len(results) <= 5

    def test_results_sorted_by_score(self):
        results = self.index.search("nginx fix")
        if len(results) > 1:
            scores = [r.score for r in results]
            assert scores == sorted(scores, reverse=True)

    def test_as_dict_has_required_fields(self):
        results = self.index.search("nginx")
        for r in results:
            d = r.as_dict()
            assert "target_type" in d
            assert "id" in d
            assert "snippet" in d
            assert "score" in d
