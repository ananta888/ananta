from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]


class CapabilityRegistry:
    """Validate and expose capability packs by domain and category."""

    def __init__(self, *, schema_path: Path | None = None, repository_root: Path | None = None) -> None:
        self.repository_root = (repository_root or ROOT).resolve()
        self.schema_path = schema_path or (self.repository_root / "schemas" / "domain" / "capability_pack.v1.json")
        self._capabilities_by_id: dict[str, dict[str, Any]] = {}
        self._capability_ids_by_domain: dict[str, set[str]] = defaultdict(set)

    def load_from_descriptors(self, descriptors: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        known_domains = set(descriptors.keys())
        for descriptor in descriptors.values():
            pack_ref = str(descriptor.get("capability_pack") or "").strip()
            if not pack_ref:
                raise ValueError(f"missing capability_pack for domain {descriptor.get('domain_id')}")
            pack_path = self._resolve_ref(pack_ref)
            self.load_pack(pack_path, known_domains=known_domains)
        return {cap_id: dict(payload) for cap_id, payload in self._capabilities_by_id.items()}

    def load_pack(self, pack_path: Path, *, known_domains: set[str]) -> dict[str, Any]:
        schema = self._load_json(self.schema_path)
        payload = self._load_json(pack_path)
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.path))
        if errors:
            readable = "; ".join(f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors)
            raise ValueError(f"invalid capability pack {pack_path}: {readable}")

        domain_id = str(payload.get("domain_id") or "").strip()
        if domain_id not in known_domains:
            raise ValueError(f"capability pack references unknown domain_id: {domain_id}")

        for capability in list(payload.get("capabilities") or []):
            capability_id = str(capability.get("capability_id") or "").strip()
            capability_domain = str(capability.get("domain_id") or "").strip()
            if capability_domain != domain_id:
                raise ValueError(f"capability {capability_id} has mismatched domain_id {capability_domain}")
            if capability_id in self._capabilities_by_id:
                raise ValueError(f"duplicate capability_id detected: {capability_id}")
            self._capabilities_by_id[capability_id] = dict(capability)
            self._capability_ids_by_domain[domain_id].add(capability_id)
        return payload

    def all_capability_ids(self) -> set[str]:
        return set(self._capabilities_by_id.keys())

    def capability(self, capability_id: str) -> dict[str, Any] | None:
        return dict(self._capabilities_by_id.get(str(capability_id).strip()) or {}) or None

    def capabilities_for_domain(self, domain_id: str) -> list[dict[str, Any]]:
        identifiers = sorted(self._capability_ids_by_domain.get(str(domain_id).strip(), set()))
        return [dict(self._capabilities_by_id[capability_id]) for capability_id in identifiers]

    def capabilities_by_category(self, domain_id: str) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for capability in self.capabilities_for_domain(domain_id):
            grouped[str(capability.get("category") or "uncategorized")].append(capability)
        return dict(grouped)

    def _resolve_ref(self, ref: str) -> Path:
        path = Path(ref)
        if path.is_absolute():
            return path
        return self.repository_root / ref

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

