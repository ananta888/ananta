from __future__ import annotations

import json

import pytest

from worker.retrieval.codecompass_output_reader import CodeCompassOutputReader, build_output_manifest


def _claim(effective: str, *, available: bool = True, verified: bool = True) -> dict[str, object]:
    return {
        "configured": True,
        "runtime_available": available,
        "verified": verified,
        "effective": effective,
    }


def _capability(detected_type: str, pipeline: str) -> dict[str, object]:
    return {
        "detected_type": detected_type,
        "pipeline": pipeline,
        "indexed": _claim("structured"),
        "symbols": _claim("parser_backed"),
        "relationships": _claim("semantic"),
        "parser_id": "fixture-parser",
        "parser_version": "1",
        "diagnostic_codes": ["z", "a", "a"],
        "file_count": 1,
    }


def test_manifest_projects_registry_coverage_and_capabilities_deterministically(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "index.jsonl").write_text(json.dumps({"file": "src/app.py"}), encoding="utf-8")
    entries = [
        _capability("text/x-python", "repository_map"),
        _capability("text/html", "rag_helper"),
    ]

    first = build_output_manifest(
        output_dir=output_dir,
        generated_at="fixed",
        file_type_registry={
            "schema_version": "file-type-registry.v1",
            "registry_version": "2026.07.18",
            "snapshot_hash": "A" * 64,
        },
        coverage={
            "manifest_candidate_count": 2,
            "indexed": 1,
            "excluded": 0,
            "unsupported": 1,
            "failed": 0,
            "diagnostic_counts": {"z": 2, "a": 1},
        },
        file_type_capabilities=entries,
    )
    second = build_output_manifest(
        output_dir=output_dir,
        generated_at="fixed",
        file_type_registry=first["file_type_registry"],
        coverage=first["coverage"],
        file_type_capabilities=list(reversed(entries)),
    )

    assert first["manifest_hash"] == second["manifest_hash"]
    assert first["file_type_registry"]["snapshot_hash"] == "a" * 64
    assert [item["detected_type"] for item in first["file_type_capabilities"]] == [
        "text/html",
        "text/x-python",
    ]
    assert first["file_type_capabilities"][0]["diagnostic_codes"] == ["a", "z"]
    assert list(first["coverage"]["diagnostic_counts"]) == ["a", "z"]


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"symbols": _claim("parser_backed", available=False, verified=False)}, "invalid_symbols_effective_claim"),
        ({"symbols": _claim("parser_backed", verified=False)}, "invalid_symbols_effective_claim"),
        ({"indexed": _claim("none"), "symbols": _claim("parser_backed")}, "symbols_require_indexed"),
        (
            {
                "indexed": _claim("none"),
                "symbols": _claim("none"),
                "relationships": _claim("semantic"),
            },
            "relationships_require_symbols",
        ),
    ],
)
def test_manifest_rejects_capability_overclaim(tmp_path, overrides, error):
    entry = _capability("text/x-python", "repository_map")
    entry.update(overrides)

    with pytest.raises(ValueError, match=error):
        build_output_manifest(output_dir=tmp_path, file_type_capabilities=[entry])


def test_manifest_rejects_duplicate_capability_truth(tmp_path):
    entry = _capability("text/x-python", "repository_map")

    with pytest.raises(ValueError, match="duplicate_file_type_capability"):
        build_output_manifest(output_dir=tmp_path, file_type_capabilities=[entry, dict(entry)])


def test_manifest_rejects_incomplete_coverage_accounting(tmp_path):
    with pytest.raises(ValueError, match="coverage_count_mismatch"):
        build_output_manifest(
            output_dir=tmp_path,
            coverage={
                "manifest_candidate_count": 2,
                "indexed": 1,
                "excluded": 0,
                "unsupported": 0,
                "failed": 0,
            },
        )


def test_manifest_rejects_negative_coverage_and_file_counts(tmp_path):
    with pytest.raises(ValueError, match="invalid_excluded"):
        build_output_manifest(
            output_dir=tmp_path,
            coverage={
                "manifest_candidate_count": 0,
                "indexed": 0,
                "excluded": -1,
                "unsupported": 0,
                "failed": 0,
            },
        )

    entry = _capability("text/x-python", "repository_map")
    entry["file_count"] = -1
    with pytest.raises(ValueError, match="invalid_file_count"):
        build_output_manifest(output_dir=tmp_path, file_type_capabilities=[entry])


def test_reader_projects_file_type_evidence_from_the_existing_manifest(tmp_path):
    evidence = {
        "file_type_registry": {
            "schema_version": "codecompass.file-type-support-registry.v1",
            "registry_version": "1.1.0",
            "snapshot_hash": "b" * 64,
        },
        "coverage": {
            "manifest_candidate_count": 1,
            "indexed": 1,
            "excluded": 0,
            "unsupported": 0,
            "failed": 0,
        },
        "file_type_capabilities": [_capability("markdown", "rag_helper")],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(evidence), encoding="utf-8")

    loaded = CodeCompassOutputReader().load_from_output_dir(output_dir=tmp_path)

    assert loaded["manifest"]["file_type_registry"] == evidence["file_type_registry"]
    assert loaded["manifest"]["coverage"]["indexed"] == 1
    assert loaded["manifest"]["file_type_capabilities"][0]["detected_type"] == "markdown"
