from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


def _schema() -> dict:
    root = Path(__file__).resolve().parents[1]
    return json.loads((root / "schemas" / "worker" / "codecompass_output_manifest.v1.json").read_text(encoding="utf-8"))


def _base_manifest() -> dict:
    return {
        "schema": "codecompass_output_manifest.v1",
        "codecompass_version": "1.0.0",
        "profile_name": "java_spring",
        "source_scope": "repo",
        "generated_at": "2026-04-29T13:00:00+02:00",
        "output_dir": "/tmp/out",
        "outputs": {
            "index": {"path": "index.jsonl", "sha256": "a", "mtime": 1.0, "record_count": 1},
            "details": {"path": "details.jsonl", "sha256": "b", "mtime": 1.0, "record_count": 1},
            "context": {"path": "context.jsonl", "sha256": "c", "mtime": 1.0, "record_count": 1},
            "embedding": {"path": "embedding.jsonl", "sha256": "d", "mtime": 1.0, "record_count": 1},
            "relations": {"path": "relations.jsonl", "sha256": "e", "mtime": 1.0, "record_count": 1},
            "graph_nodes": {"path": "graph_nodes.jsonl", "sha256": "f", "mtime": 1.0, "record_count": 1},
            "graph_edges": {"path": "graph_edges.jsonl", "sha256": "g", "mtime": 1.0, "record_count": 1},
        },
    }


def test_codecompass_manifest_schema_accepts_complete_manifest():
    schema = _schema()
    payload = _base_manifest()
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors == []


def test_codecompass_manifest_schema_accepts_partial_outputs():
    schema = _schema()
    payload = _base_manifest()
    payload["outputs"]["embedding"] = None
    payload["outputs"]["graph_nodes"] = None
    payload["outputs"]["graph_edges"] = None
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors == []


def test_codecompass_manifest_schema_accepts_additive_file_type_evidence():
    schema = _schema()
    payload = _base_manifest()
    payload["file_type_registry"] = {
        "schema_version": "file-type-registry.v1",
        "registry_version": "2026.07.18",
        "snapshot_hash": "a" * 64,
    }
    payload["coverage"] = {
        "manifest_candidate_count": 2,
        "indexed": 1,
        "excluded": 0,
        "unsupported": 1,
        "failed": 0,
        "truncated": False,
        "diagnostic_counts": {"unsupported_type": 1},
    }
    payload["file_type_capabilities"] = [
        {
            "detected_type": "text/x-python",
            "pipeline": "repository_map",
            "indexed": {
                "configured": True,
                "runtime_available": True,
                "verified": True,
                "effective": "structured",
            },
            "symbols": {
                "configured": True,
                "runtime_available": True,
                "verified": True,
                "effective": "parser_backed",
            },
            "relationships": {
                "configured": True,
                "runtime_available": True,
                "verified": True,
                "effective": "semantic",
            },
            "parser_id": "tree-sitter-python",
            "parser_version": "1",
            "fallback_reason": None,
            "diagnostic_codes": [],
            "file_count": 1,
        }
    ]

    errors = list(Draft202012Validator(schema).iter_errors(payload))

    assert errors == []


def test_codecompass_manifest_schema_rejects_missing_output_key():
    schema = _schema()
    payload = _base_manifest()
    del payload["outputs"]["relations"]
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors


def test_codecompass_manifest_schema_rejects_dimension_incompatible_effective_mode():
    schema = _schema()
    payload = _base_manifest()
    payload["file_type_capabilities"] = [
        {
            "detected_type": "python",
            "pipeline": "semantic_translation",
            "indexed": {
                "configured": True,
                "runtime_available": True,
                "verified": True,
                "effective": "semantic",
            },
            "symbols": {
                "configured": False,
                "runtime_available": False,
                "verified": False,
                "effective": "none",
            },
            "relationships": {
                "configured": False,
                "runtime_available": False,
                "verified": False,
                "effective": "none",
            },
            "file_count": 1,
        }
    ]

    assert list(Draft202012Validator(schema).iter_errors(payload))
