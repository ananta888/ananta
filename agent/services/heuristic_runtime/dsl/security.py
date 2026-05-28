"""DSL Security — Capability-Grenzen für tui_snake DSL-Heuristiken."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_SNAKE_FORBIDDEN_CAPABILITIES = frozenset({
    "network_access", "secret_access", "file_write", "send_to_worker",
    "request_context_extension", "inline_code", "shell_command",
})
_SNAKE_ALLOWED_CAPABILITIES = frozenset({
    "read_local_context", "read_artifact_refs", "read_active_task",
})


@dataclass
class CapabilityCheckResult:
    passed: bool
    violations: list[str] = field(default_factory=list)


def check_dsl_capabilities(dsl: dict[str, Any], domain: str = "tui_snake") -> CapabilityCheckResult:
    """Prüft DSL auf verbotene Capabilities für tui_snake."""
    violations: list[str] = []

    if domain not in ("tui_snake", "eclipse_snake", "snake_eclipse"):
        return CapabilityCheckResult(passed=True)

    safety = dsl.get("safety") or {}
    allowed_caps = set(safety.get("allowed_capabilities") or [])

    # Überprüfe angegebene Capabilities
    bad_caps = allowed_caps & _SNAKE_FORBIDDEN_CAPABILITIES
    for cap in sorted(bad_caps):
        violations.append(f"capability_violation:{cap}:forbidden_for_{domain}")

    # Prüfe auf verbotene Schlüssel im gesamten DSL
    forbidden_found = _find_forbidden_keys(dsl, "")
    violations.extend(forbidden_found)

    return CapabilityCheckResult(passed=len(violations) == 0, violations=violations)


def _find_forbidden_keys(obj: Any, path: str) -> list[str]:
    violations: list[str] = []
    if isinstance(obj, dict):
        for key, val in obj.items():
            full_path = f"{path}.{key}" if path else key
            if key in _SNAKE_FORBIDDEN_CAPABILITIES or key in {"eval", "exec", "import", "inline_code"}:
                violations.append(f"forbidden_key:{full_path}")
            violations.extend(_find_forbidden_keys(val, full_path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            violations.extend(_find_forbidden_keys(item, f"{path}[{i}]"))
    return violations
