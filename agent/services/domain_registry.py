from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


class DomainRegistry:
    """Load and validate domain descriptors as read-only metadata."""

    def __init__(
        self,
        *,
        domain_dirs: list[Path] | None = None,
        descriptor_schema_path: Path | None = None,
        repository_root: Path | None = None,
    ) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.domain_dirs = list(domain_dirs or [self.repository_root / "domains"])
        self.descriptor_schema_path = descriptor_schema_path or (
            self.repository_root / "schemas" / "domain" / "domain_descriptor.v1.json"
        )
        self._descriptors: dict[str, dict[str, Any]] = {}

    def load(self) -> dict[str, dict[str, Any]]:
        schema = self._load_json(self.descriptor_schema_path)
        validator = Draft202012Validator(schema)
        descriptors: dict[str, dict[str, Any]] = {}
        for descriptor_file in self._iter_descriptor_files():
            payload = self._load_json(descriptor_file)
            errors = sorted(validator.iter_errors(payload), key=lambda err: list(err.path))
            if errors:
                readable = "; ".join(f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors)
                raise ValueError(f"invalid domain descriptor {descriptor_file}: {readable}")
            domain_id = str(payload.get("domain_id") or "").strip()
            if not domain_id:
                raise ValueError(f"descriptor missing domain_id: {descriptor_file}")
            if domain_id in descriptors:
                raise ValueError(f"duplicate domain_id detected: {domain_id}")
            self._validate_descriptor_references(payload, descriptor_file=descriptor_file)
            descriptors[domain_id] = payload
        self._descriptors = descriptors
        return dict(descriptors)

    def get_descriptor(self, domain_id: str) -> dict[str, Any] | None:
        return dict(self._descriptors.get(str(domain_id).strip()) or {}) or None

    def list_domains(self) -> list[dict[str, Any]]:
        return [
            {
                "domain_id": descriptor["domain_id"],
                "display_name": descriptor["display_name"],
                "version": descriptor["version"],
                "lifecycle_status": descriptor["lifecycle_status"],
                "runtime_status": descriptor["runtime_status"],
                "supported_clients": list(descriptor.get("supported_clients") or []),
            }
            for descriptor in self._descriptors.values()
        ]

    def _iter_descriptor_files(self) -> list[Path]:
        files: list[Path] = []
        for domain_dir in self.domain_dirs:
            if not domain_dir.exists():
                continue
            files.extend(sorted(path for path in domain_dir.glob("*/domain.json") if path.is_file()))
        return files

    def _validate_descriptor_references(self, descriptor: dict[str, Any], *, descriptor_file: Path) -> None:
        required_refs = [str(descriptor.get("capability_pack") or "").strip()]
        list_refs = [
            *(descriptor.get("context_schemas") or []),
            *(descriptor.get("artifact_schemas") or []),
            *(descriptor.get("policy_packs") or []),
            *(descriptor.get("rag_profiles") or []),
        ]
        refs = [ref for ref in required_refs + [str(item).strip() for item in list_refs] if ref]
        for ref in refs:
            resolved = self._resolve_ref(ref, descriptor_file=descriptor_file)
            if not resolved.exists():
                raise ValueError(f"descriptor reference not found for {descriptor.get('domain_id')}: {ref}")

    def _resolve_ref(self, ref: str, *, descriptor_file: Path) -> Path:
        candidate = Path(ref)
        if candidate.is_absolute():
            return candidate
        if ref.startswith("domains/"):
            return self.repository_root / ref
        return descriptor_file.parent / ref

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

