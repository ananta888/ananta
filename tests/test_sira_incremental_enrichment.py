from __future__ import annotations

from pathlib import Path

from worker.retrieval.sira.incremental_enrichment import EnrichmentLayerStore, plan_incremental_enrichment


def test_incremental_plan_only_reprocesses_changed_documents():
    change_set = plan_incremental_enrichment(
        previous_documents=[
            {"record_id": "same", "document_hash": "a"},
            {"record_id": "changed", "document_hash": "b"},
            {"record_id": "deleted", "document_hash": "c"},
        ],
        current_documents=[
            {"record_id": "same", "document_hash": "a"},
            {"record_id": "changed", "document_hash": "new"},
            {"record_id": "added", "document_hash": "d"},
        ],
        previous_profile_digest="profile-a",
        current_profile_digest="profile-a",
    )
    assert change_set.unchanged_record_ids == ("same",)
    assert change_set.enrich_record_ids == ("added", "changed")
    assert change_set.tombstone_record_ids == ("deleted",)


def test_layer_activation_is_pointer_last_idempotent_and_compactable(tmp_path: Path):
    store = EnrichmentLayerStore(root=tmp_path / "layers")
    binding = {"index_digest": "index-1"}
    base = store.write_layer(
        layer_kind="base",
        parent_layer_id="",
        artifacts=[{"source_chunk_id": "a", "value": 1}, {"source_chunk_id": "b", "value": 1}],
        tombstone_record_ids=(),
        binding=binding,
    )
    duplicate = store.write_layer(
        layer_kind="base",
        parent_layer_id="",
        artifacts=[{"source_chunk_id": "a", "value": 1}, {"source_chunk_id": "b", "value": 1}],
        tombstone_record_ids=(),
        binding=binding,
    )
    assert duplicate["layer_id"] == base["layer_id"]
    delta = store.write_layer(
        layer_kind="delta",
        parent_layer_id=base["layer_id"],
        artifacts=[{"source_chunk_id": "b", "value": 2}],
        tombstone_record_ids=["a"],
        binding=binding,
    )
    store.activate(base_layer_id=base["layer_id"], delta_layer_ids=[delta["layer_id"]], binding=binding)
    assert store.materialize_active() == {"b": {"source_chunk_id": "b", "value": 2}}
    compacted = store.compact()
    assert compacted["delta_layer_ids"] == []
    assert store.diagnostics()["artifact_count"] == 1
