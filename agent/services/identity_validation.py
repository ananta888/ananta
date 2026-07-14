"""Pure validation for security-relevant Hub identity values.

Identity consumers must not normalize values implicitly.  Trimming,
truncating, or coercing another JSON type can alias two distinct principals or
resource keys.  Domain services may translate the stable reason code into
their own transport error, while sharing this exact value policy.
"""

from __future__ import annotations

from typing import Any

DEFAULT_IDENTITY_MAX_LENGTH = 160


class IdentityValidationError(ValueError):
    def __init__(self, reason_code: str, field_name: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.field_name = field_name


def require_canonical_identity(
    value: Any,
    *,
    field_name: str,
    required: bool = True,
    max_length: int = DEFAULT_IDENTITY_MAX_LENGTH,
) -> str:
    """Return an unchanged string identity or raise a stable validation error."""

    if value is None or value == "":
        if not required:
            return ""
        raise IdentityValidationError(f"{field_name}_required", field_name)
    if not isinstance(value, str):
        raise IdentityValidationError(f"{field_name}_not_canonical", field_name)
    if value != value.strip():
        raise IdentityValidationError(f"{field_name}_not_canonical", field_name)
    if len(value) > max_length:
        raise IdentityValidationError(f"{field_name}_too_long", field_name)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise IdentityValidationError(f"{field_name}_not_canonical", field_name)
    return value


__all__ = [
    "DEFAULT_IDENTITY_MAX_LENGTH",
    "IdentityValidationError",
    "require_canonical_identity",
]
