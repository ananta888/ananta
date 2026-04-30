from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "artifacts" / "workflow_integration_run_artifact.v1.json").read_text(encoding="utf-8"))


def _base(status: str, mode: str) -> dict:
    return {
        "provider": "mock",
        "workflow_id": "wf",
        "correlation_id": "c1",
        "status": status,
        "mode": mode,
        "timestamps": {"started_at": "2026-04-30T00:00:00Z"},
        "provenance": {"source": "workflow_adapter"},
    }


def test_artifact_schema_success_failed_dry_run() -> None:
    for status, mode in [("completed", "read"), ("failed", "write"), ("degraded", "dry_run")]:
        jsonschema.validate(_base(status, mode), SCHEMA)
