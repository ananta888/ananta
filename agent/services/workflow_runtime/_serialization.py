"""Compatibility import path.

The implementation lives in ``agent.common.canonical_serialization``: a dependency-free helper that every
layer may import (agent.common).
"""

from agent.common.canonical_serialization import (  # noqa: F401
    canonical_json,
    contains_sensitive_keys,
    redact_json,
    sha256_json,
)
