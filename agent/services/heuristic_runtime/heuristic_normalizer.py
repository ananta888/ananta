"""HeuristicNormalizer — canonical JSON normalizer and YAML importer.

Rules:
  - JSON is the canonical runtime format. YAML is authoring-only.
  - Normalizer sorts keys deterministically.
  - Normalizer fills defaults: status=candidate, deterministic=true (only after validation for active).
  - YAML in authoring/ cannot become active directly.
  - Runtime loads only validated JSON from heuristics/active/.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

_DEFAULT_STATUS = "candidate"
_CANONICAL_KEY_ORDER = [
    "heuristic_id", "version", "status", "domain", "description",
    "deterministic", "safety_class", "capabilities", "ttl_policy",
    "runtime", "inputs", "outputs", "parameters", "content_hash",
    "provenance", "changelog",
]


@dataclass
class NormalizeResult:
    success: bool
    normalized: dict[str, Any] | None = None
    content_hash: str = ""
    reason_code: str = ""
    warnings: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


class HeuristicNormalizer:
    """Produces canonical JSON from raw heuristic dicts (JSON or YAML source)."""

    def normalize(self, raw: dict[str, Any], *, source_format: str = "json") -> NormalizeResult:
        """Normalize a raw heuristic dict to canonical form.

        Fills defaults, sorts keys, computes content_hash.
        Does NOT set status=active — that requires activation gate.
        """
        warnings: list[str] = []
        out: dict[str, Any] = {}

        heuristic_id = str(raw.get("heuristic_id") or "").strip()
        if not heuristic_id:
            return NormalizeResult(success=False, reason_code="missing_heuristic_id")

        out["heuristic_id"] = heuristic_id
        out["version"] = str(raw.get("version") or "1.0.0").strip()

        # status: default to candidate (never auto-set to active)
        status = str(raw.get("status") or _DEFAULT_STATUS).strip().lower()
        if status == "active" and source_format == "yaml":
            status = "candidate"
            warnings.append("yaml_source_cannot_be_active: forced to candidate")
        out["status"] = status

        out["domain"] = str(raw.get("domain") or "").strip()
        out["description"] = str(raw.get("description") or "").strip()
        out["deterministic"] = bool(raw.get("deterministic", True))

        safety_class = str(raw.get("safety_class") or "bounded").strip()
        out["safety_class"] = safety_class

        caps = [str(c) for c in (raw.get("capabilities") or [])]
        out["capabilities"] = sorted(set(caps))

        ttl = raw.get("ttl_policy")
        if isinstance(ttl, dict):
            out["ttl_policy"] = dict(ttl)

        runtime = raw.get("runtime")
        if isinstance(runtime, dict):
            out["runtime"] = self._normalize_runtime(runtime)
        else:
            out["runtime"] = {"mode": "declarative_rules"}

        out["inputs"] = [str(i) for i in (raw.get("inputs") or [])]
        out["outputs"] = [str(o) for o in (raw.get("outputs") or [])]
        out["parameters"] = dict(raw.get("parameters") or {})

        # Provenance
        out["provenance"] = {
            "created_by": str(raw.get("provenance", {}).get("created_by") or "unknown"),
            "normalized_from": source_format,
            "schema_version": "heuristic_definition.v1",
        }

        out["changelog"] = list(raw.get("changelog") or [])

        # content_hash over stable fields (exclude content_hash and provenance)
        hashable = {k: v for k, v in out.items() if k not in ("content_hash", "provenance")}
        canonical_bytes = json.dumps(hashable, sort_keys=True, ensure_ascii=False).encode("utf-8")
        content_hash = hashlib.sha256(canonical_bytes).hexdigest()
        out["content_hash"] = content_hash

        return NormalizeResult(
            success=True,
            normalized=out,
            content_hash=content_hash,
            warnings=warnings,
        )

    def normalize_from_yaml(self, yaml_text: str) -> NormalizeResult:
        """Parse YAML and normalize to canonical JSON. YAML source → status always candidate."""
        try:
            import yaml  # type: ignore[import]
        except ImportError:
            return NormalizeResult(success=False, reason_code="pyyaml_not_installed")
        try:
            raw = yaml.safe_load(yaml_text)
        except Exception as exc:
            return NormalizeResult(success=False, reason_code=f"yaml_parse_error:{exc}")
        if not isinstance(raw, dict):
            return NormalizeResult(success=False, reason_code="yaml_not_a_mapping")
        return self.normalize(raw, source_format="yaml")

    def _normalize_runtime(self, runtime: dict[str, Any]) -> dict[str, Any]:
        mode = str(runtime.get("mode") or "declarative_rules")
        out: dict[str, Any] = {"mode": mode}
        if mode == "python_strategy" and "python_strategy" in runtime:
            ps = dict(runtime["python_strategy"])
            out["python_strategy"] = {
                "module": str(ps.get("module") or ""),
                "class": str(ps.get("class") or ""),
                "expected_inputs": list(ps.get("expected_inputs") or []),
                "expected_outputs": list(ps.get("expected_outputs") or []),
                "required_capabilities": list(ps.get("required_capabilities") or []),
            }
        if "triggers" in runtime:
            out["triggers"] = list(runtime["triggers"])
        if "selection" in runtime:
            out["selection"] = dict(runtime["selection"])
        if "action" in runtime:
            out["action"] = dict(runtime["action"])
        if mode == "composite_chain" and "composite_chain" in runtime:
            out["composite_chain"] = dict(runtime["composite_chain"])
        return out

    def write_normalized(self, normalized: dict[str, Any], dest_path: str) -> None:
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
