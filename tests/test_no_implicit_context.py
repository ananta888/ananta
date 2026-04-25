from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agent.services.context_schema_registry import ContextSchemaRegistry
from client_surfaces.common.context_packaging import package_editor_context

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "context" / "context_envelope.v1.json"


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def _valid_context_envelope() -> dict:
    return {
        "schema": "context_envelope.v1",
        "explicit_inputs": {
            "user_selection": {
                "text": "Refactor this function.",
                "origin": "editor_selection",
                "language": "python",
            },
            "client_captured": {
                "file_path": "/workspace/src/main.py",
                "project_root": "/workspace",
                "extra_paths": ["/workspace/src/helpers.py"],
                "rejected_paths": ["/outside/secrets.txt"],
            },
        },
        "retrieved_context": {
            "query": "refactor helper usage",
            "chunks": [
                {
                    "chunk_id": "wiki:abc123",
                    "source_ref": "artifact:doc-1#chunk-2",
                    "retrieval_reason": "query_match",
                    "content_excerpt": "Use helper module for shared parsing.",
                    "score": 0.91,
                }
            ],
        },
        "source_refs": [
            {
                "ref_id": "artifact:doc-1#chunk-2",
                "kind": "knowledge_chunk",
                "location": "knowledge://doc-1/chunk-2",
                "retrieval_reason": "query_match",
            }
        ],
        "redactions": {
            "has_secret_redactions": True,
            "redaction_rules": ["token", "password", "api_key"],
        },
        "size_limits": {
            "max_context_chars": 12000,
            "max_payload_bytes": 65536,
            "selection_clipped": False,
        },
        "context_hash": "d" * 64,
        "provenance": {
            "created_by_component": "client",
            "context_origin": {
                "has_user_input": True,
                "has_client_capture": True,
                "has_retrieval": True,
            },
            "generated_at": "2026-04-25T16:38:54+02:00",
        },
    }


def test_context_envelope_schema_rejects_unlabelled_raw_context_blobs() -> None:
    payload = _valid_context_envelope()
    assert list(_validator().iter_errors(payload)) == []

    payload["raw_context"] = "hidden_context_blob_should_not_be_allowed"
    errors = list(_validator().iter_errors(payload))
    assert errors


def test_context_envelope_requires_source_refs_and_retrieval_reasons_for_rag_chunks() -> None:
    payload = _valid_context_envelope()
    del payload["retrieved_context"]["chunks"][0]["source_ref"]
    del payload["retrieved_context"]["chunks"][0]["retrieval_reason"]

    errors = list(_validator().iter_errors(payload))
    assert errors


def test_equivalent_client_context_metadata_is_bounded_and_flags_secrets_or_unrelated_paths() -> None:
    payload = package_editor_context(
        file_path="/workspace/src/main.py",
        project_root="/workspace",
        selection_text="token=abc123\nprint('safe')",
        extra_paths=["/workspace/src/util.py", "/outside/leak.txt"],
        max_selection_chars=2000,
        max_paths=5,
    )

    assert payload["schema"] == "client_bounded_context_payload_v1"
    assert payload["bounded"] is True
    assert payload["implicit_unrelated_paths_included"] is False
    assert payload["rejected_paths"] == ["/outside/leak.txt"]
    assert "selection_may_contain_secret" in payload["warnings"]


def test_context_validation_rejects_malformed_payload_and_degrades_oversized_payload(tmp_path: Path) -> None:
    registry = ContextSchemaRegistry(repository_root=tmp_path, max_payload_bytes=120)
    registry.load_from_descriptors({"context-envelope": {"context_schemas": [str(SCHEMA_PATH)]}})

    malformed = registry.validate_context(domain_id="context-envelope", payload="not-an-object")
    oversized = registry.validate_context(
        domain_id="context-envelope",
        payload={**_valid_context_envelope(), "source_refs": [{"ref_id": "x", "kind": "url", "location": "u"}] * 50},
    )

    assert malformed["status"] == "rejected"
    assert oversized["status"] == "degraded"

