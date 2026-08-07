"""Shared wire identities for CodeCompass semantic domain partitions."""

from __future__ import annotations

import hashlib

CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD = "_ananta_semantic_partition_domain_key"
CODECOMPASS_REPOSITORY_ROOT_DOMAIN = "__repository_root__"


def codecompass_semantic_domain_key(domain: str) -> str:
    """Hash a codepoint- and whitespace-exact top-level repository domain."""

    return "sha256:" + hashlib.sha256(domain.encode("utf-8")).hexdigest()


def codecompass_semantic_repository_root_domain_key() -> str:
    """Return the namespace-separated identity for repository-root files.

    A real top-level directory named ``__repository_root__`` intentionally
    keeps the ordinary path-domain identity above.  Root-level files use this
    tagged identity so both domains can coexist without an opaque-key
    collision.
    """

    seed = "codecompass-semantic-domain.v1\0repository_root"
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


__all__ = [
    "CODECOMPASS_SEMANTIC_DOMAIN_KEY_FIELD",
    "CODECOMPASS_REPOSITORY_ROOT_DOMAIN",
    "codecompass_semantic_domain_key",
    "codecompass_semantic_repository_root_domain_key",
]
