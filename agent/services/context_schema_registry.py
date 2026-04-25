from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


class ContextSchemaRegistry:
    """Load and validate domain context payloads against registered schemas."""

    def __init__(self, *, repository_root: Path | None = None, max_payload_bytes: int = 64 * 1024) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.max_payload_bytes = max_payload_bytes
        self._validators_by_domain: dict[str, list[Draft202012Validator]] = {}

    def load_from_descriptors(self, descriptors: dict[str, dict[str, Any]]) -> None:
        validators: dict[str, list[Draft202012Validator]] = {}
        for domain_id, descriptor in descriptors.items():
            schema_refs = [
                str(item).strip()
                for item in list(descriptor.get("context_schemas") or [])
                if str(item).strip()
            ]
            domain_validators: list[Draft202012Validator] = []
            for schema_ref in schema_refs:
                schema_path = self._resolve_ref(schema_ref)
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
                domain_validators.append(Draft202012Validator(schema))
            validators[domain_id] = domain_validators
        self._validators_by_domain = validators

    def validate_context(self, *, domain_id: str, payload: Any) -> dict[str, Any]:
        normalized_domain = str(domain_id).strip()
        if normalized_domain not in self._validators_by_domain:
            return {"status": "degraded", "errors": [f"unknown_domain:{normalized_domain}"]}
        if not isinstance(payload, dict):
            return {"status": "rejected", "errors": ["context_payload_must_be_object"]}

        payload_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if payload_size > self.max_payload_bytes:
            return {
                "status": "degraded",
                "errors": [f"context_payload_too_large:{payload_size}>{self.max_payload_bytes}"],
            }

        validators = self._validators_by_domain.get(normalized_domain) or []
        if not validators:
            return {"status": "degraded", "errors": ["no_context_schema_registered"]}

        all_errors: list[str] = []
        for validator in validators:
            errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
            if not errors:
                return {"status": "accepted", "errors": []}
            all_errors.extend(f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors[:5])
        return {"status": "rejected", "errors": all_errors[:10]}

    def _resolve_ref(self, ref: str) -> Path:
        path = Path(ref)
        if path.is_absolute():
            return path
        return self.repository_root / ref
