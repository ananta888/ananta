from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


class ArtifactTypeRegistry:
    """Discover and validate artifact types without domain-specific hard-coding."""

    def __init__(self, *, schema_path: Path | None = None, repository_root: Path | None = None) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.schema_path = schema_path or (self.repository_root / "schemas" / "domain" / "artifact_type_pack.v1.json")
        self._artifact_types: dict[str, dict[str, Any]] = {}

    def load_pack(self, pack_path: Path, *, known_domains: set[str]) -> dict[str, Any]:
        schema = self._load_json(self.schema_path)
        payload = self._load_json(pack_path)
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.path))
        if errors:
            readable = "; ".join(f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors)
            raise ValueError(f"invalid artifact type pack {pack_path}: {readable}")

        domain_id = str(payload.get("domain_id") or "").strip()
        if domain_id not in known_domains:
            raise ValueError(f"artifact pack references unknown domain_id: {domain_id}")

        for artifact_type in list(payload.get("artifact_types") or []):
            artifact_type_id = str(artifact_type.get("artifact_type_id") or "").strip()
            artifact_domain = str(artifact_type.get("domain_id") or "").strip()
            if artifact_domain != domain_id:
                raise ValueError(f"artifact type {artifact_type_id} has mismatched domain_id {artifact_domain}")
            if artifact_type_id in self._artifact_types:
                raise ValueError(f"duplicate artifact_type_id detected: {artifact_type_id}")
            self._artifact_types[artifact_type_id] = dict(artifact_type)
        return payload

    def list_artifact_types(self, *, domain_id: str | None = None, client: str | None = None) -> list[dict[str, Any]]:
        normalized_domain = str(domain_id).strip() if domain_id else None
        normalized_client = str(client).strip() if client else None
        result: list[dict[str, Any]] = []
        for artifact_type in self._artifact_types.values():
            if normalized_domain and str(artifact_type.get("domain_id")) != normalized_domain:
                continue
            if normalized_client and normalized_client not in list(artifact_type.get("allowed_clients") or []):
                continue
            result.append(dict(artifact_type))
        return result

    def validate_artifact_payload(self, *, artifact_type_id: str, payload: Any) -> dict[str, Any]:
        artifact_type = self._artifact_types.get(str(artifact_type_id).strip())
        if not artifact_type:
            return {"status": "unknown", "errors": ["artifact_type_unknown"]}
        schema_ref = str(artifact_type.get("schema_ref") or "").strip()
        schema_path = self._resolve_ref(schema_ref)
        if not schema_path.exists():
            return {"status": "unsupported", "errors": [f"schema_not_found:{schema_ref}"]}
        schema = self._load_json(schema_path)
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
        if errors:
            readable = [f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors[:10]]
            return {"status": "invalid", "errors": readable}
        return {"status": "valid", "errors": []}

    def _resolve_ref(self, ref: str) -> Path:
        path = Path(ref)
        if path.is_absolute():
            return path
        return self.repository_root / ref

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

