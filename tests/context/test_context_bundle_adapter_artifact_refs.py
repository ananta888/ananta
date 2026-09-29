from __future__ import annotations

from worker.core.context_bundle_adapter import ContextEnvelopeRef


def test_context_envelope_ref_parses_artifact_refs() -> None:
    ref = ContextEnvelopeRef.from_raw(
        {
            "context_bundle_id": "ctx-1",
            "context_hash": "hash-1",
            "artifact_grant_refs": ["g1", "g2"],
            "source_usage_refs": ["u1"],
            "denied_context_refs": ["a:secret"],
        }
    )
    assert ref.bundle_id == "ctx-1"
    assert ref.artifact_grant_refs == ["g1", "g2"]
    assert ref.source_usage_refs == ["u1"]
    assert ref.denied_context_refs == ["a:secret"]
    payload = ref.as_dict()
    assert payload["artifact_grant_refs"] == ["g1", "g2"]
