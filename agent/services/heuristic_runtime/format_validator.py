"""HeuristicFormatValidator — internal consistency checks beyond JSON schema.

Validates:
  1. strategy_kind matches runtime.mode
  2. python_strategy mode requires module + class (non-empty)
  3. version is semver-like (X.Y.Z)
  4. ttl_policy.min_seconds <= ttl_policy.default_seconds <= ttl_policy.max_seconds
  5. deterministic=true is required for snake domains
  6. declared inputs/outputs are non-empty strings
  7. parameters dict values are JSON-serializable scalars or lists

reason_codes use the pattern: <check_name>:<detail>
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

_SNAKE_DOMAINS = {"tui_snake", "snake_eclipse", "eclipse_snake"}

_STRATEGY_KIND_TO_MODE: dict[str, str] = {
    "declarative": "declarative_rules",
    "python": "python_strategy",
    "composite": "composite_chain",
}
_MODE_TO_STRATEGY_KIND: dict[str, str] = {v: k for k, v in _STRATEGY_KIND_TO_MODE.items()}

_VALID_SAFETY_CLASSES = {"ui_motion_only", "readonly", "bounded", "elevated"}


@dataclass
class FormatValidationResult:
    passed: bool
    reason_codes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
            "warnings": list(self.warnings),
        }


class HeuristicFormatValidator:
    """Internal consistency validator for heuristic definition dicts.

    Does NOT load from disk — operates on already-parsed dicts.
    JSON schema validation is handled by HeuristicCatalogValidator.
    This validator checks semantic consistency.
    """

    def validate(self, hdef: dict[str, Any]) -> FormatValidationResult:
        codes: list[str] = []
        warnings: list[str] = []

        hid = str(hdef.get("heuristic_id") or "")
        if not hid:
            return FormatValidationResult(passed=False, reason_codes=["missing_heuristic_id"])

        # 1 — version semver check
        version = str(hdef.get("version") or "")
        if not _SEMVER_RE.match(version):
            codes.append(f"invalid_version_format:{version!r}")

        # 2 — safety_class valid
        safety_class = str(hdef.get("safety_class") or "")
        if safety_class not in _VALID_SAFETY_CLASSES:
            codes.append(f"invalid_safety_class:{safety_class!r}")

        # 3 — snake domains require deterministic=true
        domain = str(hdef.get("domain") or "")
        deterministic = hdef.get("deterministic", True)
        if domain in _SNAKE_DOMAINS and not deterministic:
            codes.append(f"snake_domain_must_be_deterministic:{domain}")

        # 4 — runtime consistency
        runtime = hdef.get("runtime") or {}
        if isinstance(runtime, dict):
            mode = str(runtime.get("mode") or "")
            strategy_kind = str(hdef.get("strategy_kind") or "")

            # strategy_kind ↔ mode coherence (warn only — both may be absent)
            expected_mode = _STRATEGY_KIND_TO_MODE.get(strategy_kind)
            if strategy_kind and expected_mode and mode and mode != expected_mode:
                warnings.append(
                    f"strategy_kind_mode_mismatch: strategy_kind={strategy_kind!r} "
                    f"implies mode={expected_mode!r} but runtime.mode={mode!r}"
                )

            # python_strategy mode requires module + class
            if mode == "python_strategy":
                ps = runtime.get("python_strategy") or {}
                if isinstance(ps, dict):
                    module = str(ps.get("module") or "").strip()
                    cls = str(ps.get("class") or "").strip()
                    if not module:
                        codes.append("python_strategy_missing_module")
                    if not cls:
                        codes.append("python_strategy_missing_class")
                    if module and not module.startswith("agent.heuristics.strategies."):
                        warnings.append(
                            f"python_strategy_module_outside_strategies_package:{module!r}"
                        )
                else:
                    codes.append("python_strategy_block_not_a_dict")

        # 5 — ttl_policy consistency
        ttl = hdef.get("ttl_policy")
        if isinstance(ttl, dict):
            mn = ttl.get("min_seconds")
            default = ttl.get("default_seconds")
            mx = ttl.get("max_seconds")
            if all(isinstance(v, (int, float)) for v in [mn, default, mx]):
                if not (mn <= default <= mx):  # type: ignore[operator]
                    codes.append(
                        f"ttl_policy_invariant_violated:min={mn} default={default} max={mx}"
                    )
                if mn <= 0:
                    codes.append(f"ttl_min_must_be_positive:{mn}")

        # 6 — inputs/outputs are lists of non-empty strings
        for field_name in ("inputs", "outputs"):
            items = hdef.get(field_name) or []
            if not isinstance(items, list):
                codes.append(f"{field_name}_must_be_list")
            else:
                for i, item in enumerate(items):
                    if not isinstance(item, str) or not item.strip():
                        codes.append(f"{field_name}[{i}]_empty_or_non_string")

        # 7 — parameters values must be JSON-serializable
        params = hdef.get("parameters") or {}
        if not isinstance(params, dict):
            codes.append("parameters_must_be_dict")
        else:
            for k, v in params.items():
                try:
                    json.dumps(v)
                except (TypeError, ValueError):
                    codes.append(f"parameter_not_serializable:{k!r}")

        # 8 — description must be non-empty
        description = str(hdef.get("description") or "").strip()
        if not description:
            warnings.append("missing_description")

        passed = len(codes) == 0
        return FormatValidationResult(passed=passed, reason_codes=codes, warnings=warnings)
