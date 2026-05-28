"""Tests für Heuristic Proposal Store (T06.04)."""
from __future__ import annotations

import json
import os
import tempfile
import time

import pytest

from agent.services.heuristic_runtime.heuristic_proposal_store import (
    HeuristicProposalStore,
    StoredProposal,
)


def _valid_dsl(rationale="test"):
    return {
        "dsl_version": "2.0",
        "observe": {"sources": ["tui.snapshot"]},
        "action": {"kind": "follow_artifact", "confidence": 0.8},
        "safety": {"safety_class": "ui_motion_only"},
        "provenance": {"created_by": "lab", "rationale": rationale},
    }


@pytest.fixture
def store(tmp_path):
    return HeuristicProposalStore(candidates_dir=str(tmp_path))


class TestHeuristicProposalStore:
    def test_store_returns_proposal(self, store):
        dsl = _valid_dsl()
        proposal = store.store(dsl)
        assert proposal is not None
        assert proposal.proposal_id.startswith("lab_")
        assert proposal.status == "candidate"

    def test_status_is_always_candidate(self, store):
        proposal = store.store(_valid_dsl())
        assert proposal.status == "candidate"
        # Never active
        assert proposal.status != "active"

    def test_duplicate_returns_none(self, store):
        dsl = _valid_dsl()
        p1 = store.store(dsl)
        p2 = store.store(dsl)
        assert p1 is not None
        assert p2 is None  # duplicate

    def test_different_dsl_both_stored(self, store):
        p1 = store.store(_valid_dsl(rationale="reason-1"))
        p2 = store.store(_valid_dsl(rationale="reason-2"))
        assert p1 is not None
        assert p2 is not None

    def test_file_written_to_disk(self, tmp_path):
        store = HeuristicProposalStore(candidates_dir=str(tmp_path))
        proposal = store.store(_valid_dsl())
        files = list(tmp_path.glob("*.heuristic_proposal.json"))
        assert len(files) == 1

    def test_load_returns_stored_proposal(self, store):
        dsl = _valid_dsl()
        proposal = store.store(dsl)
        loaded = store.load(proposal.proposal_id)
        assert loaded is not None
        assert loaded.proposal_id == proposal.proposal_id
        assert loaded.status == "candidate"

    def test_load_nonexistent_returns_none(self, store):
        result = store.load("nonexistent-id")
        assert result is None

    def test_list_proposals_returns_ids(self, store):
        p1 = store.store(_valid_dsl(rationale="r1"))
        p2 = store.store(_valid_dsl(rationale="r2"))
        ids = store.list_proposals()
        assert len(ids) == 2
        assert p1.proposal_id in ids
        assert p2.proposal_id in ids

    def test_list_proposals_empty_dir(self, store):
        assert store.list_proposals() == []

    def test_model_stored_in_proposal(self, store):
        proposal = store.store(_valid_dsl(), model="lmstudio-7b")
        assert proposal.model == "lmstudio-7b"

    def test_source_snapshot_hashes_stored(self, store):
        hashes = ["abc123", "def456"]
        proposal = store.store(_valid_dsl(), source_snapshot_hashes=hashes)
        assert proposal.source_snapshot_hashes == hashes

    def test_content_hash_deduplication_across_instances(self, tmp_path):
        """Same directory, different store instances — dedup still works."""
        store1 = HeuristicProposalStore(candidates_dir=str(tmp_path))
        dsl = _valid_dsl()
        p1 = store1.store(dsl)
        # New instance loads existing hashes
        store2 = HeuristicProposalStore(candidates_dir=str(tmp_path))
        p2 = store2.store(dsl)
        assert p1 is not None
        assert p2 is None  # duplicate detected

    def test_file_content_is_valid_json(self, tmp_path):
        store = HeuristicProposalStore(candidates_dir=str(tmp_path))
        proposal = store.store(_valid_dsl())
        files = list(tmp_path.glob("*.heuristic_proposal.json"))
        with open(files[0], encoding="utf-8") as f:
            data = json.load(f)
        assert data["proposal_id"] == proposal.proposal_id
        assert data["status"] == "candidate"

    def test_provenance_extracted_from_dsl(self, store):
        dsl = _valid_dsl()
        proposal = store.store(dsl)
        assert proposal.provenance.get("created_by") == "lab"

    def test_created_at_is_recent(self, store):
        before = time.time()
        proposal = store.store(_valid_dsl())
        after = time.time()
        assert before <= proposal.created_at <= after
