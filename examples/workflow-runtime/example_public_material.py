"""Dependency-free public material for the isolated runtime example.

These derived values are test fixtures, not credentials. They intentionally
provide no production authority.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

EXAMPLE_KEY_ID = "public-example-key-v1"
EXAMPLE_COMMAND_KEY_ID = "public-example-command-key-v1"
EXAMPLE_TASK_QUEUE = "ananta-workflow-runtime-example-v1"
HUB_EVENTS_FILE = "workflow-runtime-example-hub-events-v1.jsonl"


def example_signing_key() -> bytes:
    return hashlib.sha256(b"ananta-public-workflow-runtime-example-v1").digest()


def example_command_signing_key() -> str:
    """Base64 Ed25519 private seed for the example's workflow commands.

    Commands are checked by the worker's public-key authority Activity, so the
    example issues them asymmetrically the way production does.  Task
    authorization envelopes keep the shared-secret key above: the worker must be
    able to verify a command without being able to forge one.
    """

    seed = hashlib.sha256(b"ananta-public-workflow-runtime-example-command-v1").digest()
    return base64.b64encode(seed).decode("ascii")


def example_command_public_key() -> str:
    """Base64 Ed25519 public key derived from the command signing seed.

    Kept derived rather than hard-coded so the pair can never drift apart.
    """

    private_key = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(example_command_signing_key().encode("ascii"), validate=True),
    )
    public_material = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return base64.b64encode(public_material).decode("ascii")


def example_bearer() -> str:
    return hashlib.sha256(b"ananta-public-example-hub-boundary-v1").hexdigest()


__all__ = [
    "EXAMPLE_COMMAND_KEY_ID",
    "EXAMPLE_KEY_ID",
    "EXAMPLE_TASK_QUEUE",
    "HUB_EVENTS_FILE",
    "example_bearer",
    "example_command_public_key",
    "example_command_signing_key",
    "example_signing_key",
]
