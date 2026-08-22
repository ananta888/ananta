"""Closed JSON-Schema validation for HRM experiment boundary objects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker


class HrmContractValidationError(ValueError):
    """Raised when an object crosses an HRM boundary with an invalid shape."""

    def __init__(self, contract_name: str, violations: tuple[str, ...]) -> None:
        self.contract_name = contract_name
        self.violations = violations
        super().__init__(f"invalid {contract_name} contract: {'; '.join(violations)}")


class HrmContractValidator:
    """Validate named HRM contracts without duplicating their source schema."""

    def __init__(self, schema_path: Path | None = None) -> None:
        path = schema_path or (
            Path(__file__).resolve().parents[3]
            / "schemas"
            / "hrm-experiments"
            / "contracts.v1.json"
        )
        with path.open("r", encoding="utf-8") as schema_file:
            schema = json.load(schema_file)
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict):
            raise ValueError("HRM contract schema has no $defs object")
        self._definitions = frozenset(str(name) for name in definitions)
        self._schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$defs": definitions,
        }
        self._format_checker = FormatChecker()

    def validate(self, contract_name: str, value: Mapping[str, Any]) -> None:
        """Validate one named closed contract and report bounded error paths."""

        if contract_name not in self._definitions:
            raise ValueError(f"unknown HRM contract: {contract_name}")
        validator = Draft202012Validator(
            {**self._schema, "$ref": f"#/$defs/{contract_name}"},
            format_checker=self._format_checker,
        )
        errors = sorted(
            validator.iter_errors(value),
            key=lambda error: tuple(str(part) for part in error.absolute_path),
        )
        if not errors:
            return
        violations = tuple(
            f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: {error.message}"
            for error in errors[:16]
        )
        raise HrmContractValidationError(contract_name, violations)


default_hrm_contract_validator = HrmContractValidator()


__all__ = [
    "HrmContractValidationError",
    "HrmContractValidator",
    "default_hrm_contract_validator",
]
