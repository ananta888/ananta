"""Operator-invoked local Hub key preparation; no network, grant or trust activation."""

import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.services.meet_machine_key_files import install_key_file, key_file_exists, private_key_directory
from agent.services.meet_signing_key import load_meet_signing_key, read_meet_key_file

PRIVATE = "machine-private.pem"
PUBLIC = "machine-public.pem"


def _provision(directory_fd):
    private_exists = key_file_exists(directory_fd, PRIVATE)
    public_exists = key_file_exists(directory_fd, PUBLIC)
    if public_exists and not private_exists:
        raise ValueError("meet_machine_key_pair_incomplete")
    if private_exists:
        key = load_meet_signing_key(PRIVATE, dir_fd=directory_fd, follow_symlinks=False)
    else:
        key = Ed25519PrivateKey.generate()
        install_key_file(
            directory_fd,
            PRIVATE,
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            ),
        )
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    if public_exists:
        existing = read_meet_key_file(PUBLIC, dir_fd=directory_fd, require_private=False, follow_symlinks=False)
        if existing != public:
            raise ValueError("meet_machine_public_key_mismatch")
    else:
        install_key_file(directory_fd, PUBLIC, public)
    # Revalidate the complete persisted pair before returning a public receipt.
    installed = load_meet_signing_key(PRIVATE, dir_fd=directory_fd, follow_symlinks=False)
    installed_public = installed.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if (
        installed_public != public
        or read_meet_key_file(PUBLIC, dir_fd=directory_fd, require_private=False, follow_symlinks=False) != public
    ):
        raise ValueError("meet_machine_key_pair_changed")
    der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return {
        "schema": "ananta.meet-machine-key-provisioning.v1",
        "status": "ready",
        "public_key_sha256": hashlib.sha256(der).hexdigest(),
        "trust_activated": False,
        "production_release_evidence": False,
    }


def provision_meet_machine_keys(directory):
    try:
        with private_key_directory(directory) as descriptor:
            return _provision(descriptor)
    except (OSError, TypeError):
        raise ValueError("meet_machine_key_provisioning_unavailable") from None
