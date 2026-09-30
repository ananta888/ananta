"""Dependency-free content-free policy shared by semantic-media evidence and audit.

The forbidden key fragments and the ``assert_content_free`` guard are pure
value checks. They live in the model layer so that persistence adapters
(``agent.repositories``) and Hub services can both enforce the same policy
without the repository layer depending on ``agent.services``.
``agent.services.semantic_media_program_evidence`` re-exports these objects
unchanged for existing importers.
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

FORBIDDEN_KEY_FRAGMENTS = frozenset(
    {
        "audio",
        "ciphertext",
        "content",
        "embedding",
        "feature_vector",
        "frame",
        "key_material",
        "local_path",
        "media",
        "password",
        "payload",
        "pixel",
        "prompt",
        "raw_text",
        "secret",
        "token_value",
        "transcript",
    }
)


class ProgramEvidenceError(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def assert_content_free(value: Any, *, known_secrets: Sequence[str] = (), path: tuple[str, ...] = ()) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold()
            if any(fragment in normalized for fragment in FORBIDDEN_KEY_FRAGMENTS):
                raise ProgramEvidenceError(f"content_field_forbidden:{'.'.join((*path, str(key)))}")
            assert_content_free(nested, known_secrets=known_secrets, path=(*path, str(key)))
        return
    if isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_content_free(nested, known_secrets=known_secrets, path=(*path, str(index)))
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise ProgramEvidenceError("non_finite_evidence_value")
    if isinstance(value, str):
        if len(value) > 512 or any(secret and secret in value for secret in known_secrets):
            raise ProgramEvidenceError("secret_or_unbounded_evidence_value")
        if value.startswith(("/", "file:", "~")) or "\\" in value:
            raise ProgramEvidenceError("absolute_path_in_evidence")


__all__ = ["FORBIDDEN_KEY_FRAGMENTS", "ProgramEvidenceError", "assert_content_free"]
