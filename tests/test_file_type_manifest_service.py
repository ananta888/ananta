from __future__ import annotations

from pathlib import Path

from agent.services.file_type_manifest_service import FileTypeManifestService
from ananta_contracts.file_type_support import load_file_type_support_registry
from worker.retrieval.codecompass_output_reader import build_output_manifest


def _service() -> FileTypeManifestService:
    registry = load_file_type_support_registry(Path(__file__).resolve().parents[1])
    requirements = {
        requirement
        for descriptor in registry.descriptors
        for pipeline in registry.pipelines
        for dimension in ("indexed", "symbols", "relationships")
        for requirement in getattr(descriptor.support_for(pipeline), dimension).runtime_requirements
    }
    return FileTypeManifestService(
        registry=registry,
        runtime_availability={requirement: True for requirement in requirements},
    )


def test_rag_helper_manifest_projection_preserves_outcomes_and_capability_truth(tmp_path):
    service = _service()

    enriched = service.enrich_rag_helper_manifest(
        {
            "files": [
                {
                    "file": "README.md",
                    "size": 100,
                    "stats": {
                        "detail_count": 2,
                        "relation_count": 1,
                        "diagnostic_codes": [],
                    },
                },
                {
                    "file": "docs/broken.md",
                    "skipped": True,
                    "skip_reason": "max_file_size_kb_exceeded",
                },
            ]
        }
    )

    assert [item["detected_type"] for item in enriched["files"]] == [
        "markdown",
        "markdown",
    ]
    assert enriched["coverage"] == {
        "manifest_candidate_count": 2,
        "indexed": 1,
        "excluded": 1,
        "unsupported": 0,
        "failed": 0,
        "truncated": False,
        "diagnostic_counts": {"max_file_size_kb_exceeded": 1},
    }
    claim = enriched["file_type_capabilities"][0]
    assert claim["detected_type"] == "markdown"
    assert claim["pipeline"] == "rag_helper"
    assert claim["file_count"] == 2
    assert claim["indexed"]["effective"] in {"plain_text", "structured"}

    # The worker-side canonical reader accepts exactly the projected contract.
    manifest = build_output_manifest(
        output_dir=tmp_path,
        file_type_registry=enriched["file_type_registry"],
        coverage=enriched["coverage"],
        file_type_capabilities=enriched["file_type_capabilities"],
    )
    assert manifest["coverage"]["manifest_candidate_count"] == 2
