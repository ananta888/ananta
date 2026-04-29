from __future__ import annotations

from worker.retrieval.codecompass_cache_state import build_codecompass_cache_state, should_invalidate_channel


def test_codecompass_cache_state_invalidation_by_manifest_and_model():
    prev = build_codecompass_cache_state(
        manifest_hash="m1",
        profile_name="java",
        retrieval_engine_version="r1",
        embedding_model_version="e1",
    )
    nxt_manifest = build_codecompass_cache_state(
        manifest_hash="m2",
        profile_name="java",
        retrieval_engine_version="r1",
        embedding_model_version="e1",
    )
    nxt_embedding = build_codecompass_cache_state(
        manifest_hash="m1",
        profile_name="java",
        retrieval_engine_version="r1",
        embedding_model_version="e2",
    )

    assert should_invalidate_channel(previous_state=prev, next_state=nxt_manifest, channel="fts") is True
    assert should_invalidate_channel(previous_state=prev, next_state=nxt_embedding, channel="vector") is True
    assert should_invalidate_channel(previous_state=prev, next_state=prev, channel="graph") is False


def test_codecompass_cache_state_invalidation_on_deleted_output():
    state = build_codecompass_cache_state(
        manifest_hash="m1",
        profile_name="java",
        retrieval_engine_version="r1",
        embedding_model_version="e1",
    )
    assert should_invalidate_channel(previous_state=state, next_state=state, channel="fts", output_file_deleted=True) is True

