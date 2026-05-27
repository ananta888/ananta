"""HeuristicCatalogValidator — validates bootstrap heuristic JSON files against schema.

Validates all .heuristic.json files in a catalog directory against
heuristic_definition.v1.json. Reports per-file results; never modifies files.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_PATH = Path(__file__).parent.parent.parent.parent / "schemas" / "heuristic" / "heuristic_definition.v1.json"
_FORBIDDEN_CAPS = frozenset({"file_write", "network_access", "secret_access"})
_SNAKE_DOMAINS = frozenset({"tui_snake", "snake_eclipse", "snake_tui"})
_SNAKE_FORBIDDEN_CAPS = frozenset({
    "write_local_notes", "send_to_chat", "read_source_refs",
    "file_write", "network_access", "secret_access",
})


@dataclass
class FileValidationResult:
    file: str
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class CatalogValidationResult:
    total: int = 0
    passed: int = 0
    failed: int = 0
    results: list[FileValidationResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def summary(self) -> str:
        return f"{self.passed}/{self.total} passed, {self.failed} failed"


class HeuristicCatalogValidator:
    """Validates all .heuristic.json files in a directory against the v1 schema."""

    def __init__(self, schema_path: str | None = None) -> None:
        path = Path(schema_path) if schema_path else _SCHEMA_PATH
        self._schema: dict[str, Any] = json.loads(path.read_text())

    def validate_file(self, path: str) -> FileValidationResult:
        fn = os.path.basename(path)
        errors: list[str] = []
        warnings: list[str] = []

        try:
            with open(path) as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return FileValidationResult(file=fn, passed=False, errors=[f"invalid_json:{e}"])

        # Schema validation
        try:
            jsonschema.validate(instance=data, schema=self._schema)
        except jsonschema.ValidationError as e:
            errors.append(f"schema:{e.message}")
        except jsonschema.SchemaError as e:
            errors.append(f"schema_error:{e.message}")

        # Bootstrap-specific rules
        domain = str(data.get("domain") or "")
        caps = set(data.get("capabilities") or [])

        # Rule: no forbidden capabilities in any bootstrap
        bad_caps = caps & _FORBIDDEN_CAPS
        if bad_caps:
            errors.append(f"forbidden_capabilities:{sorted(bad_caps)}")

        # Rule: snake domains must be deterministic
        if domain in _SNAKE_DOMAINS and not data.get("deterministic"):
            errors.append("snake_domain_must_be_deterministic")

        # Rule: snake domains must not have forbidden caps
        if domain in _SNAKE_DOMAINS:
            snake_bad = caps & _SNAKE_FORBIDDEN_CAPS
            if snake_bad:
                errors.append(f"snake_forbidden_caps:{sorted(snake_bad)}")

        # Rule: python_strategy mode must have module + class
        runtime = data.get("runtime") or {}
        if runtime.get("mode") == "python_strategy":
            py = runtime.get("python_strategy") or {}
            if not py.get("module"):
                errors.append("python_strategy:missing_module")
            if not py.get("class"):
                errors.append("python_strategy:missing_class")

        # Warning: missing description
        if not data.get("description"):
            warnings.append("missing_description")

        # Warning: missing ttl_policy
        if not data.get("ttl_policy"):
            warnings.append("missing_ttl_policy")

        return FileValidationResult(
            file=fn,
            passed=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def validate_directory(self, directory: str) -> CatalogValidationResult:
        result = CatalogValidationResult()
        for fn in sorted(os.listdir(directory)):
            if not fn.endswith(".heuristic.json"):
                continue
            path = os.path.join(directory, fn)
            fr = self.validate_file(path)
            result.total += 1
            if fr.passed:
                result.passed += 1
            else:
                result.failed += 1
            result.results.append(fr)
        return result
