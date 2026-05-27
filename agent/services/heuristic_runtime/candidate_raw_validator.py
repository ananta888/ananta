"""Candidate raw-data protection validator (ASH-013).

Blocks candidates that contain raw screen data, prompts, source code, or secrets.
Reason code on rejection: raw_content_forbidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Fields and patterns that must never appear in a candidate file
_FORBIDDEN_FIELD_NAMES: frozenset[str] = frozenset({
    "raw_screen",
    "raw_prompt",
    "raw_content",
    "source_code",
    "secret",
    "password",
    "token",
    "api_key",
    "private_key",
    "access_token",
})

_FORBIDDEN_KEY_SUBSTRINGS: tuple[str, ...] = (
    "raw_",
    "_secret",
    "_token",
    "_password",
    "_key",
    "credentials",
)


@dataclass
class RawValidationResult:
    passed: bool
    reason_codes: list[str] = field(default_factory=list)
    forbidden_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
            "forbidden_fields": list(self.forbidden_fields),
        }


class CandidateRawValidator:
    """Validates that a candidate JSON dict contains no raw sensitive data."""

    def validate(self, candidate: dict[str, Any]) -> RawValidationResult:
        forbidden: list[str] = []
        self._scan_dict(candidate, path="", found=forbidden)

        if forbidden:
            return RawValidationResult(
                passed=False,
                reason_codes=["raw_content_forbidden"],
                forbidden_fields=forbidden,
            )
        return RawValidationResult(passed=True)

    def _scan_dict(self, obj: Any, *, path: str, found: list[str]) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                full_path = f"{path}.{k}" if path else k
                if self._is_forbidden_key(str(k)):
                    found.append(full_path)
                else:
                    self._scan_dict(v, path=full_path, found=found)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                self._scan_dict(item, path=f"{path}[{i}]", found=found)
        # scalar values are not scanned for content — only field names are checked

    @staticmethod
    def _is_forbidden_key(key: str) -> bool:
        lower = key.lower()
        if lower in _FORBIDDEN_FIELD_NAMES:
            return True
        return any(sub in lower for sub in _FORBIDDEN_KEY_SUBSTRINGS)


# Module-level singleton
_validator = CandidateRawValidator()


def validate_candidate_raw_data(candidate: dict[str, Any]) -> RawValidationResult:
    """Convenience function: validate a candidate dict for raw sensitive data."""
    return _validator.validate(candidate)
