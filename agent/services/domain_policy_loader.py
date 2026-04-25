from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from agent.services.capability_registry import CapabilityRegistry

ROOT = Path(__file__).resolve().parents[2]


class DomainPolicyLoader:
    """Load domain policy packs and enforce safe defaults."""

    def __init__(
        self,
        *,
        capability_registry: CapabilityRegistry,
        schema_path: Path | None = None,
        repository_root: Path | None = None,
    ) -> None:
        self.capability_registry = capability_registry
        self.repository_root = (repository_root or ROOT).resolve()
        self.schema_path = schema_path or (self.repository_root / "schemas" / "domain" / "policy_pack.v1.json")

    def load_pack(self, pack_path: Path, *, known_domains: set[str]) -> dict[str, Any]:
        schema = self._load_json(self.schema_path)
        payload = self._load_json(pack_path)
        errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda err: list(err.path))
        if errors:
            readable = "; ".join(f"{'.'.join(map(str, err.path)) or '<root>'}: {err.message}" for err in errors)
            raise ValueError(f"invalid policy pack {pack_path}: {readable}")

        domain_id = str(payload.get("domain_id") or "").strip()
        if domain_id not in known_domains:
            raise ValueError(f"policy pack references unknown domain_id: {domain_id}")

        known_capabilities = self.capability_registry.all_capability_ids()
        for rule in list(payload.get("rules") or []):
            capability_id = str(rule.get("capability_id") or "").strip()
            if capability_id not in known_capabilities:
                raise ValueError(f"policy rule references unknown capability_id: {capability_id}")
        return payload

    def load_for_domain(self, *, domain_id: str, policy_refs: list[str], known_domains: set[str]) -> dict[str, Any]:
        normalized_domain = str(domain_id).strip()
        if not policy_refs:
            return self._safe_default_policy(normalized_domain, reason="policy_pack_missing")

        merged_rules: list[dict[str, Any]] = []
        default_decision = "default_deny"
        try:
            for policy_ref in policy_refs:
                pack = self.load_pack(self._resolve_ref(policy_ref), known_domains=known_domains)
                default_decision = str(pack.get("default_decision") or default_decision)
                merged_rules.extend(list(pack.get("rules") or []))
        except Exception as exc:
            return self._safe_default_policy(normalized_domain, reason=f"policy_pack_invalid:{exc}")
        return {
            "schema": "policy_pack.v1",
            "domain_id": normalized_domain,
            "version": "merged",
            "default_decision": default_decision,
            "rules": merged_rules,
            "status": "loaded",
        }

    @staticmethod
    def _safe_default_policy(domain_id: str, *, reason: str) -> dict[str, Any]:
        return {
            "schema": "policy_pack.v1",
            "domain_id": domain_id,
            "version": "safe-default",
            "default_decision": "default_deny",
            "rules": [],
            "status": "degraded",
            "reason": reason,
        }

    def _resolve_ref(self, ref: str) -> Path:
        path = Path(ref)
        if path.is_absolute():
            return path
        return self.repository_root / ref

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

