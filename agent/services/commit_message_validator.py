from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

ALLOWED_TYPES: frozenset[str] = frozenset({
    "feat", "fix", "security", "test", "docs",
    "refactor", "chore", "perf", "ci",
})

BLOCKED_SUBJECTS: tuple[str, ...] = (
    "fixup planning",
    "fixup",
    "wip",
    "update code",
    "fix stuff",
    "minor fix",
    "various changes",
    "changes",
)

_FORMAT_RE = re.compile(
    r"^(?P<type>[a-z]+)(\((?P<scope>[a-z0-9._/-]+)\))?!?: (?P<subject>.{1,72})$"
)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    parsed_type: Optional[str] = None
    parsed_scope: Optional[str] = None
    parsed_subject: Optional[str] = None


class CommitMessageValidator:
    def validate(self, message: str) -> ValidationResult:
        msg = str(message or "").strip()
        errors: list[str] = []

        lower = msg.lower()
        for blocked in BLOCKED_SUBJECTS:
            if lower == blocked or lower.startswith(blocked + " ") or lower.startswith(blocked + ":"):
                errors.append(f"blocked subject: '{blocked}' is not allowed as a commit message")
                return ValidationResult(valid=False, errors=errors)

        m = _FORMAT_RE.match(msg)
        if not m:
            errors.append(
                "format error: expected '<type>(<scope>): <subject>' with subject max 72 chars"
            )
            return ValidationResult(valid=False, errors=errors)

        parsed_type = m.group("type")
        parsed_scope = m.group("scope")
        parsed_subject = m.group("subject")

        if parsed_type not in ALLOWED_TYPES:
            errors.append(
                f"invalid type '{parsed_type}': must be one of {sorted(ALLOWED_TYPES)}"
            )

        subject_lower = parsed_subject.lower()
        for blocked in BLOCKED_SUBJECTS:
            if subject_lower == blocked or subject_lower.startswith(blocked + " "):
                errors.append(f"blocked subject: '{blocked}' is not allowed as subject")
                break

        if len(parsed_subject) > 72:
            errors.append(f"subject too long ({len(parsed_subject)} chars, max 72)")

        if errors:
            return ValidationResult(valid=False, errors=errors)

        return ValidationResult(
            valid=True,
            parsed_type=parsed_type,
            parsed_scope=parsed_scope,
            parsed_subject=parsed_subject,
        )

    def validate_or_raise(self, message: str) -> ValidationResult:
        result = self.validate(message)
        if not result.valid:
            raise ValueError("; ".join(result.errors))
        return result


_VALIDATOR = CommitMessageValidator()


def get_commit_message_validator() -> CommitMessageValidator:
    return _VALIDATOR
