"""Admit exactly the already pinned private test certificate for Node fetch."""

import base64
import hashlib
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def require_fixture_certificate(certificate, spki):
    path = Path(certificate)
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 16384:
        raise ValueError("test_browser_certificate_invalid")
    raw = path.read_bytes()
    try:
        cert = x509.load_pem_x509_certificate(raw)
        encoded = cert.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        if (
            raw != cert.public_bytes(Encoding.PEM)
            or base64.b64encode(hashlib.sha256(encoded).digest()).decode() != spki
        ):
            raise ValueError()
    except ValueError:
        raise ValueError("test_browser_certificate_invalid") from None
    return path.resolve()
