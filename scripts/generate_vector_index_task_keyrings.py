#!/usr/bin/env python3
"""Generate separate Hub-signing and Worker-verification Ed25519 keyrings."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from ananta_contracts.runtime_authorization_crypto import (
    ED25519_ALGORITHM,
    ED25519_SIGNING_KEYRING_SCHEMA,
    ED25519_VERIFICATION_KEYRING_SCHEMA,
)

_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _write_exclusive(path: Path, payload: dict[str, object], mode: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    descriptor = os.open(path, flags, mode)
    try:
        try:
            encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
            view = memoryview(encoded)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("vector_index_task_keyring_write_failed")
                view = view[written:]
            os.fsync(descriptor)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    finally:
        os.close(descriptor)


def generate(*, output_dir: Path, key_id: str) -> tuple[Path, Path]:
    normalized_key_id = str(key_id or "").strip()
    if _KEY_ID.fullmatch(normalized_key_id) is None:
        raise ValueError("vector_index_task_key_id_invalid")
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    if output_dir.is_symlink() or not output_dir.is_dir():
        raise ValueError("vector_index_task_keyring_output_invalid")

    private_key = Ed25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    private_value = base64.b64encode(private_raw).decode("ascii")
    public_value = base64.b64encode(public_raw).decode("ascii")

    signing_path = output_dir / "vector-index-task-signing-keyring.json"
    verification_path = output_dir / "vector-index-task-verification-keyring.json"
    signing = {
        "schema": ED25519_SIGNING_KEYRING_SCHEMA,
        "algorithm": ED25519_ALGORITHM,
        "active_key_id": normalized_key_id,
        "private_keys": {normalized_key_id: private_value},
        "public_keys": {normalized_key_id: public_value},
        "revoked_key_ids": [],
        "revoked_envelope_ids": [],
    }
    verification = {
        "schema": ED25519_VERIFICATION_KEYRING_SCHEMA,
        "algorithm": ED25519_ALGORITHM,
        "public_keys": {normalized_key_id: public_value},
        "revoked_key_ids": [],
        "revoked_envelope_ids": [],
    }
    _write_exclusive(signing_path, signing, 0o600)
    try:
        _write_exclusive(verification_path, verification, 0o644)
    except BaseException:
        signing_path.unlink(missing_ok=True)
        raise
    return signing_path, verification_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../ananta-secrets/vector-index"),
    )
    parser.add_argument(
        "--key-id",
        default="vector-index-task-key-v1",
    )
    arguments = parser.parse_args()
    signing_path, verification_path = generate(
        output_dir=arguments.output_dir,
        key_id=arguments.key_id,
    )
    print(signing_path)
    print(verification_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
