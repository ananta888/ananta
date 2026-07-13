"""Dependency-free public material for the isolated runtime example.

These derived values are test fixtures, not credentials. They intentionally
provide no production authority.
"""

from __future__ import annotations

import hashlib

EXAMPLE_KEY_ID = "public-example-key-v1"
EXAMPLE_TASK_QUEUE = "ananta-workflow-runtime-example-v1"
HUB_EVENTS_FILE = "workflow-runtime-example-hub-events-v1.jsonl"


def example_signing_key() -> bytes:
    return hashlib.sha256(b"ananta-public-workflow-runtime-example-v1").digest()


def example_bearer() -> str:
    return hashlib.sha256(b"ananta-public-example-hub-boundary-v1").hexdigest()


__all__ = [
    "EXAMPLE_KEY_ID",
    "EXAMPLE_TASK_QUEUE",
    "HUB_EVENTS_FILE",
    "example_bearer",
    "example_signing_key",
]
