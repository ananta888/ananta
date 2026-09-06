#!/usr/bin/env python3
"""Provision only this optional worker's private key and pinned open voice assets."""

import argparse
import hashlib
import os
import secrets
import urllib.request
from pathlib import Path

REVISION = "1162a9173d0ce503555aed757976b7a9912eae4c"
BASE = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/de/de_DE/thorsten/medium/"
FILES = {
    "de_DE-thorsten-medium.onnx": "7e64762d8e5118bb578f2eea6207e1a35a8e0c30595010b666f983fc87bb7819",
    "de_DE-thorsten-medium.onnx.json": "974adee790533adb273a1ac88f49027d2a1b8f0f2cf4905954a4791e79264e85",
}


def provision(directory):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077:
        raise ValueError("runtime_directory_must_be_private")
    key = directory / "worker-key"
    if not key.exists():
        descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(secrets.token_hex(32).encode())
    if key.stat().st_mode & 0o077:
        raise ValueError("worker_key_must_be_private")
    models = directory / "models"
    models.mkdir(exist_ok=True)
    (directory / "worker-state").mkdir(mode=0o700, exist_ok=True)
    for name, expected in FILES.items():
        target = models / name
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("existing_voice_digest_mismatch")
            continue
        with urllib.request.urlopen(BASE + name, timeout=60) as response:
            content = response.read(70_000_001)
        if len(content) > 70_000_000 or hashlib.sha256(content).hexdigest() != expected:
            raise ValueError("downloaded_voice_digest_mismatch")
        with target.open("xb") as output:
            output.write(content)
    print("Private worker key and pinned German voice ready. No credentials printed.")


def provision_machine_keys(directory):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_path, public_path = directory / "machine-private.pem", directory / "machine-public.pem"
    if private_path.exists() or public_path.exists():
        if not private_path.exists() or not public_path.exists():
            raise ValueError("machine_key_pair_incomplete")
        if private_path.stat().st_mode & 0o077:
            raise ValueError("machine_private_key_permissions")
        key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("machine_private_key_type")
        expected = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        if public_path.read_bytes() != expected:
            raise ValueError("machine_public_key_mismatch")
        return
    key = Ed25519PrivateKey.generate()
    with os.fdopen(os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as output:
        output.write(
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    with public_path.open("xb") as output:
        output.write(
            key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        )
    print("Machine key pair ready. Public trust was not activated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--machine-keys", action="store_true")
    args = parser.parse_args()
    if not args.directory.is_absolute():
        parser.error("directory must be absolute")
    provision(args.directory)
    if args.machine_keys:
        provision_machine_keys(args.directory)
