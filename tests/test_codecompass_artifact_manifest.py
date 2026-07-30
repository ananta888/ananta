from __future__ import annotations

import pytest

from agent.services.codecompass_artifact_manifest import (
    CodeCompassArtifactManifestError,
    CodeCompassArtifactManifestProjector,
)


def _references():
    return [
        {
            "role": "manifest",
            "filename": "manifest.json",
            "artifact_schema": "knowledge_index_manifest.v1",
            "media_type": "application/json",
            "size_bytes": 100,
            "sha256": "a" * 64,
            "local_path": "/must/not/leak/manifest.json",
        },
        {
            "role": "index",
            "filename": "index.jsonl",
            "artifact_schema": "knowledge_index_records.v1",
            "media_type": "application/jsonl",
            "size_bytes": 200,
            "sha256": "b" * 64,
            "worker_url": "https://must-not-leak.invalid",
        },
    ]


def test_public_manifest_projects_roles_coverage_without_host_paths() -> None:
    manifest = CodeCompassArtifactManifestProjector().project(
        knowledge_index_id="index-example",
        run_id="run-example",
        source_revision_id="revision-example",
        references=_references(),
        coverage={
            "symbol_total": 10,
            "symbol_indexed": 8,
            "vector_total": 4,
            "vector_indexed": 4,
        },
        exclusions=[
            {
                "reason_code": "unsupported_type",
                "relative_path": "assets/image.bin",
            }
        ],
    )

    payload = manifest.to_dict()
    assert payload["coverage"]["symbol_ratio"] == 0.8
    assert len(payload["manifest_digest"]) == 64
    serialized = str(payload)
    assert "local_path" not in serialized
    assert "worker_url" not in serialized
    assert "/must/not/leak" not in serialized


def test_graph_artifacts_require_complete_valid_binding() -> None:
    graph = {
        "role": "graph_index",
        "filename": "cc_graph_index.json",
        "artifact_schema": "codecompass_graph_index.v1",
        "media_type": "application/json",
        "size_bytes": 100,
        "sha256": "c" * 64,
    }
    with pytest.raises(
        CodeCompassArtifactManifestError,
        match="graph_artifacts_incomplete",
    ):
        CodeCompassArtifactManifestProjector().project(
            knowledge_index_id="index-example",
            run_id="run-example",
            source_revision_id="revision-example",
            references=[*_references(), graph],
        )


@pytest.mark.parametrize(
    "reference",
    (
        {**_references()[0], "filename": "../../manifest.json"},
        {**_references()[0], "sha256": "not-a-digest"},
        {**_references()[0], "size_bytes": 1024 * 1024 * 1024},
    ),
)
def test_malformed_artifact_reference_is_fail_closed(reference) -> None:
    with pytest.raises(CodeCompassArtifactManifestError):
        CodeCompassArtifactManifestProjector().project(
            knowledge_index_id="index-example",
            run_id="run-example",
            source_revision_id="revision-example",
            references=[reference, _references()[1]],
        )


def test_coverage_and_exclusions_are_bounded() -> None:
    with pytest.raises(CodeCompassArtifactManifestError):
        CodeCompassArtifactManifestProjector().project(
            knowledge_index_id="index-example",
            run_id="run-example",
            source_revision_id="revision-example",
            references=_references(),
            coverage={"symbol_total": 1, "symbol_indexed": 2},
        )
    with pytest.raises(CodeCompassArtifactManifestError):
        CodeCompassArtifactManifestProjector().project(
            knowledge_index_id="index-example",
            run_id="run-example",
            source_revision_id="revision-example",
            references=_references(),
            exclusions=[
                {"reason_code": "excluded", "relative_path": "../secret"}
            ],
        )
