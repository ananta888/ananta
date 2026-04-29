#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "core_provider_boundary.json"


@dataclass(frozen=True)
class BoundaryViolation:
    path: str
    violation_type: str
    detail: str
    line: int | None = None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check provider-specific leakages in configured core modules.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to boundary config JSON.")
    parser.add_argument("--mode", choices=("report", "strict"), default="report")
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_paths(*, root: Path, entries: list[str]) -> list[Path]:
    paths: list[Path] = []
    for entry in entries:
        candidate = (root / str(entry)).resolve()
        if candidate.is_dir():
            paths.extend(sorted(candidate.rglob("*.py")))
        elif candidate.is_file():
            paths.append(candidate)
    return paths


def _is_import_allowlisted(module_name: str, allowlist: list[str]) -> bool:
    lowered = str(module_name or "").strip().lower()
    for prefix in allowlist:
        normalized = str(prefix or "").strip().lower()
        if normalized and (lowered == normalized or lowered.startswith(f"{normalized}.")):
            return True
    return False


def _match_forbidden_term(text: str, forbidden_terms: list[str]) -> str | None:
    lowered = str(text or "").lower()
    for term in forbidden_terms:
        normalized = str(term or "").strip().lower()
        if not normalized:
            continue
        if normalized in lowered:
            return normalized
    return None


def _line_allowlisted(line: str, allowlist_patterns: list[str]) -> bool:
    lowered = str(line or "").lower()
    for pattern in allowlist_patterns:
        normalized = str(pattern or "").strip().lower()
        if normalized and normalized in lowered:
            return True
    return False


def _collect_import_violations(
    *,
    root: Path,
    path: Path,
    forbidden_terms: list[str],
    allowlist_import_prefixes: list[str],
) -> list[BoundaryViolation]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[BoundaryViolation] = []
    for node in ast.walk(tree):
        module_name = ""
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = str(alias.name or "")
                term = _match_forbidden_term(module_name, forbidden_terms)
                if term and not _is_import_allowlisted(module_name, allowlist_import_prefixes):
                    violations.append(
                        BoundaryViolation(
                            path=str(path.relative_to(root)),
                            violation_type="forbidden_import",
                            detail=f"{module_name} (term={term})",
                            line=getattr(node, "lineno", None),
                        )
                    )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            module_name = str(node.module)
            term = _match_forbidden_term(module_name, forbidden_terms)
            if term and not _is_import_allowlisted(module_name, allowlist_import_prefixes):
                violations.append(
                    BoundaryViolation(
                        path=str(path.relative_to(root)),
                        violation_type="forbidden_import",
                        detail=f"{module_name} (term={term})",
                        line=getattr(node, "lineno", None),
                    )
                )
    return violations


def _collect_string_violations(
    *,
    root: Path,
    path: Path,
    forbidden_terms: list[str],
    allowlist_string_patterns: list[str],
) -> list[BoundaryViolation]:
    violations: list[BoundaryViolation] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if _line_allowlisted(line, allowlist_string_patterns):
            continue
        term = _match_forbidden_term(line, forbidden_terms)
        if term is None:
            continue
        violations.append(
            BoundaryViolation(
                path=str(path.relative_to(root)),
                violation_type="forbidden_string",
                detail=f"term={term}",
                line=index,
            )
        )
    return violations


def check_core_provider_boundaries(*, root: Path, config_path: Path) -> list[BoundaryViolation]:
    payload = _load_json(config_path)
    modules = [str(item) for item in list(payload.get("core_modules_for_checks") or []) if str(item).strip()]
    forbidden_terms = [str(item) for item in list(payload.get("forbidden_terms") or []) if str(item).strip()]
    allowlist_import_prefixes = [
        str(item) for item in list(payload.get("allowlist_import_prefixes") or []) if str(item).strip()
    ]
    allowlist_string_patterns = [
        str(item) for item in list(payload.get("allowlist_string_patterns") or []) if str(item).strip()
    ]
    paths = _resolve_paths(root=root, entries=modules)
    violations: list[BoundaryViolation] = []
    for path in paths:
        violations.extend(
            _collect_import_violations(
                root=root,
                path=path,
                forbidden_terms=forbidden_terms,
                allowlist_import_prefixes=allowlist_import_prefixes,
            )
        )
        violations.extend(
            _collect_string_violations(
                root=root,
                path=path,
                forbidden_terms=forbidden_terms,
                allowlist_string_patterns=allowlist_string_patterns,
            )
        )
    return violations


def main() -> int:
    args = _parse_args()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (ROOT / config_path).resolve()
    violations = check_core_provider_boundaries(root=ROOT, config_path=config_path)
    if violations:
        print("core-provider-boundary-violations")
        for item in violations:
            line = f":{item.line}" if item.line else ""
            print(f"- {item.path}{line} [{item.violation_type}] {item.detail}")
        print("reference: docs/decisions/ADR-core-boundary-and-provider-plugins.md")
        print("reference: agent/providers/interfaces.py")
        if args.mode == "strict":
            return 1
        return 0
    print("core-provider-boundary-check-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
