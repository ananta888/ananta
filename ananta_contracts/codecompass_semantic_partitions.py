"""Shared wire identities for CodeCompass semantic domain partitions."""

from __future__ import annotations

import hashlib

CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD = "_ananta_semantic_partition_domain_key"


def codecompass_semantic_domain_key(domain: str) -> str:
    """Hash a codepoint- and whitespace-exact top-level repository domain."""

    return "sha256:" + hashlib.sha256(domain.encode("utf-8")).hexdigest()


__all__ = [
    "CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD",
    "codecompass_semantic_domain_key",
]
