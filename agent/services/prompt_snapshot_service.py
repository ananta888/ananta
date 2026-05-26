from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.services.prompt_redaction_service import get_redaction_service

SCHEMA_FILE = Path(__file__).resolve().parents[2] / "schemas" / "prompts" / "prompt_template_snapshot.v1.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate(payload: dict[str, Any]) -> list[str]:
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
    return [f"{'/'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors]


class PromptSnapshotService:
    def build_template_snapshot(
        self,
        *,
        prompt_template_ref: str,
        template_path: str,
        template_version: str,
        template_text: str,
        renderer: str,
        expected_output_schema_ref: str,
        human_description: str = "",
        intended_modes: list[str] | None = None,
    ) -> dict[str, Any]:
        snapshot = {
            "schema": "prompt_template_snapshot.v1",
            "prompt_template_ref": str(prompt_template_ref),
            "template_path": str(template_path),
            "template_version": str(template_version),
            "template_hash": _sha(str(template_text)),
            "renderer": str(renderer),
            "created_at": _now_iso(),
            "expected_output_schema_ref": str(expected_output_schema_ref),
        }
        if human_description:
            snapshot["human_description"] = str(human_description)[:400]
        if intended_modes:
            snapshot["intended_modes"] = [str(item) for item in intended_modes if str(item).strip()]
        errors = _validate(snapshot)
        if errors:
            raise ValueError(f"invalid_prompt_template_snapshot:{'; '.join(errors)}")
        return snapshot

    def build_final_prompt_record(
        self,
        *,
        prompt_template_ref: str,
        variables_payload: dict[str, Any],
        final_prompt_text: str,
        context_hash: str,
        input_usage_refs: list[str] | None,
        output_schema_ref: str,
        store_raw_prompt: bool = False,
    ) -> dict[str, Any]:
        rendered = str(final_prompt_text)
        variables_hash = _sha(json.dumps(variables_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        final_hash = _sha(rendered)
        redaction = get_redaction_service().redact(rendered)
        raw_stored = bool(store_raw_prompt and redaction.redaction_count == 0)
        record = {
            "schema": "final_prompt_record.v1",
            "final_prompt_ref": f"final-prompt-{final_hash[:16]}",
            "prompt_template_ref": str(prompt_template_ref),
            "variables_hash": variables_hash,
            "final_prompt_hash": final_hash,
            "redaction_applied": bool(redaction.redaction_count > 0),
            "raw_prompt_stored": raw_stored,
            "storage_ref": f"prompt:redacted:{final_hash[:16]}",
            "context_hash": str(context_hash),
            "input_usage_refs": list(input_usage_refs or []),
            "output_schema_ref": str(output_schema_ref),
        }
        return record
