from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "schemas" / "integrations" / "workflow_descriptor.v1.json").read_text(encoding="utf-8"))


def test_workflow_descriptor_schema_accepts_generic_webhook() -> None:
    payload = {
        "provider": "generic_webhook",
        "workflow_id": "wf-a",
        "display_name": "A",
        "capability": "read",
        "risk_class": "low",
        "default_mode": "dry_run",
        "approval_required": False,
        "dry_run_supported": True,
        "callback_required": False,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    jsonschema.validate(payload, SCHEMA)


def test_workflow_descriptor_schema_accepts_minimal_n8n() -> None:
    payload = {
        "provider": "n8n",
        "workflow_id": "wf-n8n",
        "display_name": "N8N",
        "capability": "write",
        "risk_class": "high",
        "default_mode": "disabled",
        "approval_required": True,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    jsonschema.validate(payload, SCHEMA)
