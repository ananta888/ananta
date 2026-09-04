"""Worker-side allowlist for Hub-selected verification targets."""

from __future__ import annotations

import json
from pathlib import Path

from ananta_contracts.verification import VerificationAssignmentV1, validate_verification_targets


class VerificationTargetPolicy:
    """Revalidate grammar and catalog membership at the execution boundary."""

    def __init__(self, catalog_path: Path) -> None:
        self._catalog_path = catalog_path

    def authorize(self, assignment: VerificationAssignmentV1) -> None:
        targets = validate_verification_targets(assignment.backend, assignment.target_symbols)
        payload = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        if payload.get("schema") != "ananta.verification-property-catalog.v1":
            raise ValueError("verification_target_catalog_invalid")
        if assignment.backend in {"hypothesis", "crosshair_backend"}:
            allowed = {str(item) for item in payload.get("pytest_targets", [])}
        else:
            allowed = {str(item["symbol"]) for item in payload.get("candidates", []) if item.get("eligible") is True}
            allowed.update(str(item) for item in payload.get("fixture_symbols", []))
        if any(target not in allowed for target in targets):
            raise ValueError("verification_target_not_allowlisted")


__all__ = ["VerificationTargetPolicy"]
