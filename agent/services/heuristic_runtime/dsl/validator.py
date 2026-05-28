"""DSL v2 Validator — Schema-Validierung + Capability-Checks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_FORBIDDEN_KEYS = frozenset({"inline_code", "shell_command", "exec", "eval", "import"})
_ALLOWED_ACTION_KINDS = frozenset({
    "suggest_target", "follow_artifact", "lurk_near", "smooth_follow",
    "fast_target", "explain_target", "no_action"
})
_ALLOWED_SOURCES = frozenset({
    "tui.snapshot", "tui.delta", "tui.semantic", "tui.mouse", "tui.focus", "tui.history"
})


@dataclass
class ValidationResult:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def error_summary(self) -> str:
        return "; ".join(self.errors)


class DslValidator:
    def validate(self, dsl: dict[str, Any]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        # dsl_version
        if dsl.get("dsl_version") != "2.0":
            errors.append(f"dsl_version muss '2.0' sein, ist {dsl.get('dsl_version')!r}")

        # observe.sources
        observe = dsl.get("observe") or {}
        sources = observe.get("sources") or []
        unknown_sources = set(sources) - _ALLOWED_SOURCES
        if unknown_sources:
            errors.append(f"Unbekannte observe.sources: {sorted(unknown_sources)}")

        # action.kind
        action = dsl.get("action") or {}
        action_kind = action.get("kind")
        if action_kind not in _ALLOWED_ACTION_KINDS:
            errors.append(f"Unbekannte action.kind: {action_kind!r}. Erlaubt: {sorted(_ALLOWED_ACTION_KINDS)}")

        # safety.safety_class
        safety = dsl.get("safety") or {}
        safety_class = safety.get("safety_class")
        if safety_class not in ("ui_motion_only", "readonly"):
            errors.append(f"safety.safety_class muss 'ui_motion_only' oder 'readonly' sein")

        # provenance
        provenance = dsl.get("provenance") or {}
        if not provenance.get("created_by"):
            errors.append("provenance.created_by fehlt")
        if not provenance.get("rationale"):
            errors.append("provenance.rationale fehlt")

        # Verbotene Schlüssel
        self._check_forbidden(dsl, errors, path="")

        # lease TTL
        lease = dsl.get("lease") or {}
        ttl = lease.get("ttl_seconds")
        if ttl is not None:
            if float(ttl) > 120.0:
                errors.append(f"lease.ttl_seconds={ttl} überschreitet Maximum 120.0")

        # experiment TTL
        experiment = dsl.get("experiment") or {}
        exp_ttl = experiment.get("max_ttl_seconds")
        if exp_ttl is not None and float(exp_ttl) > 20.0:
            warnings.append(f"experiment.max_ttl_seconds={exp_ttl} sollte ≤20s für experimental_live sein")

        return ValidationResult(passed=len(errors) == 0, errors=errors, warnings=warnings)

    def _check_forbidden(self, obj: Any, errors: list[str], path: str) -> None:
        if isinstance(obj, dict):
            for key, val in obj.items():
                full_path = f"{path}.{key}" if path else key
                if key in _FORBIDDEN_KEYS:
                    errors.append(f"Verbotener Schlüssel: {full_path}")
                self._check_forbidden(val, errors, full_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._check_forbidden(item, errors, f"{path}[{i}]")
