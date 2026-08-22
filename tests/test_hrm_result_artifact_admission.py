from __future__ import annotations

from pathlib import Path

from agent.services.hrm_experiments.artifact_store import HrmArtifactStoreAdapter


def test_result_blob_is_verified_without_safetensors_parsing(monkeypatch) -> None:
    adapter = HrmArtifactStoreAdapter.__new__(HrmArtifactStoreAdapter)
    monkeypatch.setattr(
        adapter,
        "_verified_artifact",
        lambda content_digest, **_kwargs: (
            Path("/not-opened"),
            "application/json",
            17,
            content_digest,
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_inspect_safetensors",
        lambda *_args: (_ for _ in ()).throw(AssertionError("must not parse")),
    )

    inspection = adapter.inspect_result_digest("a" * 64)

    assert inspection.verified is True
    assert inspection.media_type == "application/json"
    assert inspection.size_bytes == 17
    assert inspection.format_name is None
