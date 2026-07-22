"""Offline/admin-side Ed25519 enrollment request builder.

The private key is generated locally with mode 0600 and never included in the
request body.  Transporting the request remains an administrator action.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Mapping

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def create_enrollment_request(
    *,
    identity_id: str,
    pool_id: str,
    instance_id: str,
    proof_nonce: str,
    private_key_path: Path,
) -> Mapping[str, str]:
    if private_key_path.exists():
        raise FileExistsError("turn_observer_private_key_already_exists")
    private_key = Ed25519PrivateKey.generate()
    raw_private = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    private_key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(private_key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
        stream.write(base64.b64encode(raw_private).decode("ascii") + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    proof_fields = {
        "identity_id": identity_id,
        "instance_id": instance_id,
        "pool_id": pool_id,
        "proof_nonce": proof_nonce,
        "public_key": base64.b64encode(public_key).decode("ascii"),
    }
    canonical = json.dumps(proof_fields, sort_keys=True, separators=(",", ":")).encode("ascii")
    signature = private_key.sign(b"ananta.turn-observer-proof.v1\x00" + canonical)
    return {**proof_fields, "proof_signature": base64.b64encode(signature).decode("ascii")}

