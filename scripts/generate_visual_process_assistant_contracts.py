#!/usr/bin/env python3
"""Generate stable Visual Process Assistant schemas and parity vectors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ananta_contracts.visual_process_assistant import (
    EditorContextEnvelope,
    HelpResponse,
    WorkflowPatch,
    canonical_context_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "visual_process"
VECTOR_FILE = ROOT / "tests" / "fixtures" / "visual_process" / "context_canonicalization.v1.json"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _schema(model: type, name: str) -> dict[str, Any]:
    payload = model.model_json_schema(mode="validation")
    payload["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    payload["$id"] = f"https://ananta.local/schemas/visual_process/{name}"
    return payload


def _vector_inputs() -> list[tuple[str, dict[str, Any]]]:
    return [
        ("empty", {}),
        ("key-order", {"z": 1, "a": 2}),
        ("nested-key-order", {"outer": {"b": 2, "a": 1}}),
        ("nfc-value", {"name": "Cafe\u0301"}),
        ("nfc-key", {"Cafe\u0301": "value"}),
        ("null-distinct", {"present": None}),
        ("booleans", {"false": False, "true": True}),
        ("safe-integer-max", {"value": 9_007_199_254_740_991}),
        ("safe-integer-negative", {"value": -42}),
        ("float-integral", {"value": 1.0}),
        ("float-fraction", {"value": 12.5}),
        ("float-negative-zero", {"value": -0.0}),
        ("float-small", {"value": 0.000000001}),
        ("array-order-preserved", {"values": [3, 1, 2]}),
        ("steps-domain-sort", {"steps": [{"id": "b"}, {"id": "a"}]}),
        (
            "edges-domain-sort",
            {"edges": [{"id": "e2", "source": "b", "target": "c"}, {"id": "e1", "source": "a", "target": "b"}]},
        ),
        ("validation-domain-sort", {"validation_issues": [{"path": "/b", "code": "z"}, {"path": "/a", "code": "a"}]}),
        (
            "evidence-domain-sort",
            {
                "evidence_refs": [
                    {"evidence_id": "e2", "source_version": "1"},
                    {"evidence_id": "e1", "source_version": "1"},
                ]
            },
        ),
        ("volatile-created-at", {"stable": 1, "created_at": "ignored"}),
        ("volatile-duration", {"duration_ms": 10, "stable": "yes"}),
        ("volatile-dom", {"dom_id": "node-1", "entity_id": "step-1"}),
        ("unicode-supplementary", {"😀": "emoji", "ä": "umlaut", "a": "ascii"}),
        ("escaped-quote", {"text": 'say "hi"'}),
        ("escaped-backslash", {"path": "a\\b"}),
        ("escaped-newline", {"text": "a\nb"}),
        ("empty-containers", {"array": [], "object": {}}),
        ("mixed-array", {"items": [None, True, 1, 1.25, "x", {"b": 2, "a": 1}]}),
        (
            "graph-excerpt",
            {
                "graph_excerpt": {
                    "steps": [{"id": "s2", "position": {"x": 2.5, "y": 0}}, {"id": "s1", "position": {"x": 1, "y": 0}}],
                    "edges": [],
                }
            },
        ),
        ("null-versus-empty", {"missing_semantics": None, "empty": ""}),
        ("locale-text", {"locale": "de-DE", "summary": "Änderung prüfen"}),
    ]


def main() -> None:
    schemas = (
        (EditorContextEnvelope, "editor_context.v1.json"),
        (HelpResponse, "help_response.v1.json"),
        (WorkflowPatch, "workflow_patch.v1.json"),
    )
    for model, name in schemas:
        _write_json(SCHEMA_DIR / name, _schema(model, name))

    vectors: list[dict[str, Any]] = []
    for name, input_value in _vector_inputs():
        canonical = canonical_context_bytes(input_value)
        vectors.append(
            {
                "name": name,
                "input": input_value,
                "canonical_utf8": canonical.decode("utf-8"),
                "sha256": hashlib.sha256(canonical).hexdigest(),
            }
        )
    _write_json(
        VECTOR_FILE,
        {
            "schema": "ananta.visual_process.context_canonicalization_vectors.v1",
            "algorithm": "ananta-editor-context-canonical-json-v1",
            "vectors": vectors,
        },
    )


if __name__ == "__main__":
    main()
