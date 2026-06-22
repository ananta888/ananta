"""
HCCA-005 — Tests for CCRStore and CCREntry.

All classes live in agent.services.context_compression.ccr_store which
already exists, so these tests should pass immediately.
"""
from __future__ import annotations

import time

import pytest

from agent.services.context_compression.ccr_store import CCRStore, CCREntry


class TestCCRStore:
    def test_store_and_retrieve(self, tmp_path):
        """Store content, retrieve by ref → same content returned."""
        store = CCRStore(store_path=tmp_path / "ccr")
        content = "Hello, this is test content for the CCR store."
        entry = store.store(content, content_type="tool_output")

        assert isinstance(entry, CCREntry)
        retrieved = store.retrieve(entry.ref)
        assert retrieved == content

    def test_retrieve_returns_none_for_unknown_ref(self, tmp_path):
        """Unknown ref → retrieve returns None."""
        store = CCRStore(store_path=tmp_path / "ccr")
        result = store.retrieve("nonexistent_ref_abc123456789")
        assert result is None

    def test_expire_removes_old_entries(self, tmp_path):
        """Storing with ttl_hours=0 makes the entry immediately expired."""
        store = CCRStore(store_path=tmp_path / "ccr", ttl_hours=0)
        entry = store.store("some content that will expire", content_type="log")

        # Entry should be in index but expired
        removed = store.expire_old()
        assert removed >= 1

        # Retrieve should return None after expiry
        result = store.retrieve(entry.ref)
        assert result is None

    def test_store_rejects_oversized_content(self, tmp_path):
        """Content exceeding max_bytes_per_item raises ValueError."""
        store = CCRStore(store_path=tmp_path / "ccr", max_bytes_per_item=100)
        oversized = "x" * 200

        with pytest.raises(ValueError, match="exceeds max_bytes_per_item"):
            store.store(oversized, content_type="tool_output")

    def test_ref_is_deterministic(self, tmp_path):
        """Same content stored twice produces the same ref."""
        store = CCRStore(store_path=tmp_path / "ccr")
        content = "deterministic content string 12345"

        entry1 = store.store(content, content_type="log")
        entry2 = store.store(content, content_type="log")

        assert entry1.ref == entry2.ref
        assert entry1.content_hash == entry2.content_hash

    def test_diagnostics_returns_dict(self, tmp_path):
        """diagnostics() returns a dict that includes 'entry_count' or 'live_entries'."""
        store = CCRStore(store_path=tmp_path / "ccr")
        store.store("some content", content_type="tool_output")

        diag = store.diagnostics()
        assert isinstance(diag, dict)
        # Accept either key name for forward compatibility
        has_count_key = "entry_count" in diag or "live_entries" in diag
        assert has_count_key, f"diagnostics missing entry count key; got keys: {list(diag.keys())}"

    def test_store_creates_directory(self, tmp_path):
        """CCRStore auto-creates its store_path directory if it does not exist."""
        deep_path = tmp_path / "deep" / "nested" / "ccr"
        assert not deep_path.exists()

        store = CCRStore(store_path=deep_path)
        assert deep_path.exists()

    def test_entry_fields_populated(self, tmp_path):
        """Stored entry has expected fields populated."""
        store = CCRStore(store_path=tmp_path / "ccr")
        content = "checking entry fields"
        entry = store.store(content, content_type="search_results", redacted=True)

        assert entry.content_type == "search_results"
        assert entry.redacted is True
        assert entry.byte_size == len(content.encode("utf-8"))
        assert entry.expires_at > entry.stored_at

    def test_exists_returns_false_for_expired(self, tmp_path):
        """exists() returns False for an expired ref."""
        store = CCRStore(store_path=tmp_path / "ccr", ttl_hours=0)
        entry = store.store("expiring content", content_type="log")
        # With ttl_hours=0, entry expires immediately
        assert store.exists(entry.ref) is False

    def test_exists_returns_true_for_live_entry(self, tmp_path):
        """exists() returns True for a freshly stored entry."""
        store = CCRStore(store_path=tmp_path / "ccr", ttl_hours=72)
        entry = store.store("live content", content_type="log")
        assert store.exists(entry.ref) is True
