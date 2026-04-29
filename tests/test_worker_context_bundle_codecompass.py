from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator


def test_worker_context_bundle_schema_accepts_codecompass_provenance_payload():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "schemas" / "worker" / "worker_context_bundle.v1.json").read_text(encoding="utf-8"))
    payload = {
        "schema": "worker_context_bundle.v1",
        "bundle_type": "worker_execution_context",
        "query": "retry timeout payment",
        "context_text": "bounded context text only",
        "chunk_count": 2,
        "token_estimate": 24,
        "chunks": [
            {
                "engine": "codecompass_fts",
                "source": "src/PaymentService.java",
                "content": "retry timeout method",
                "score": 1.1,
                "metadata": {
                    "record_id": "method:PaymentService.retryTimeout",
                    "record_kind": "java_method",
                    "file": "src/PaymentService.java",
                    "source_manifest_hash": "mh-1",
                },
            },
            {
                "engine": "codecompass_graph",
                "source": "src/PaymentController.java",
                "content": "controller uses service",
                "score": 0.8,
                "metadata": {
                    "record_id": "type:PaymentController",
                    "record_kind": "java_type",
                    "file": "src/PaymentController.java",
                    "expanded_from": "method:PaymentService.retryTimeout",
                    "relation_path": "calls_probable_target",
                    "source_manifest_hash": "mh-1",
                },
            },
        ],
        "context_policy": {"mode": "balanced"},
        "selection_trace": {"fusion": {"mode": "deterministic_v2"}},
    }
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors == []
    assert payload["context_text"] == "bounded context text only"
    assert payload["chunks"][1]["metadata"]["expanded_from"] == "method:PaymentService.retryTimeout"

