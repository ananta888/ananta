"""SS02.01: Lokale Device-Key-Verwaltung für Shared Sessions.

Private Keys verlassen niemals das lokale Gerät.
Public Key und Fingerprint sind abrufbar.
Key-Rotation erzeugt neuen Fingerprint und deaktiviert alte Session-Keys.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        NoEncryption,
        PrivateFormat,
        PublicFormat,
    )
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False


def _default_key_dir() -> Path:
    raw = str(os.environ.get("ANANTA_DEVICE_KEY_DIR") or "").strip()
    if raw:
        return Path(raw)
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config_home / "ananta" / "device-keys"


def _key_path(key_dir: Path) -> Path:
    return key_dir / "device_key.pem"


def _meta_path(key_dir: Path) -> Path:
    return key_dir / "device_key.json"


def _fingerprint_from_pubkey_bytes(pub_bytes: bytes) -> str:
    digest = hashlib.sha256(pub_bytes).hexdigest()
    # Groups of 8, separated by colon for readability
    return ":".join(digest[i:i+8] for i in range(0, 64, 8))


def _restrict_permissions(path: Path) -> None:
    try:
        if path.is_dir():
            path.chmod(stat.S_IRWXU)  # 0o700 für Verzeichnisse
        else:
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0o600 für Dateien
    except OSError:
        pass


class DeviceKeyError(Exception):
    pass


class DeviceKeyManager:
    def __init__(self, key_dir: Path | None = None) -> None:
        self._key_dir = key_dir or _default_key_dir()

    def key_exists(self) -> bool:
        return _key_path(self._key_dir).exists() and _meta_path(self._key_dir).exists()

    def generate_key(self) -> dict[str, Any]:
        """Erzeugt neuen Device-Key. Gibt öffentliche Metadaten zurück."""
        if not _CRYPTO_AVAILABLE:
            return self._generate_key_stub()
        self._key_dir.mkdir(parents=True, exist_ok=True)
        _restrict_permissions(self._key_dir)
        priv = Ed25519PrivateKey.generate()
        priv_bytes = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        pub_bytes = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        fp = _fingerprint_from_pubkey_bytes(pub_bytes)
        pub_b64 = priv.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
        key_id = str(uuid.uuid4())
        now = time.time()
        meta = {
            "key_id": key_id,
            "fingerprint": fp,
            "public_key_pem": pub_b64,
            "created_at": now,
            "algorithm": "Ed25519",
        }
        key_file = _key_path(self._key_dir)
        meta_file = _meta_path(self._key_dir)
        key_file.write_bytes(priv_bytes)
        _restrict_permissions(key_file)
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        _restrict_permissions(meta_file)
        return meta

    def _generate_key_stub(self) -> dict[str, Any]:
        """Fallback wenn cryptography nicht installiert ist (Tests, Dev)."""
        self._key_dir.mkdir(parents=True, exist_ok=True)
        _restrict_permissions(self._key_dir)
        key_id = str(uuid.uuid4())
        now = time.time()
        stub_bytes = os.urandom(32)
        fp = _fingerprint_from_pubkey_bytes(stub_bytes)
        meta = {
            "key_id": key_id,
            "fingerprint": fp,
            "public_key_pem": "STUB:" + stub_bytes.hex(),
            "created_at": now,
            "algorithm": "stub",
        }
        _key_path(self._key_dir).write_text("STUB:" + stub_bytes.hex(), encoding="utf-8")
        _restrict_permissions(_key_path(self._key_dir))
        _meta_path(self._key_dir).write_text(json.dumps(meta, indent=2), encoding="utf-8")
        _restrict_permissions(_meta_path(self._key_dir))
        return meta

    def get_public_info(self) -> dict[str, Any]:
        """Gibt öffentliche Metadaten zurück (kein Private Key)."""
        meta_file = _meta_path(self._key_dir)
        if not meta_file.exists():
            raise DeviceKeyError("No device key found. Generate one first.")
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise DeviceKeyError(f"Device key metadata corrupted: {exc}") from exc
        return {
            "key_id": str(meta.get("key_id") or ""),
            "fingerprint": str(meta.get("fingerprint") or ""),
            "public_key_pem": str(meta.get("public_key_pem") or ""),
            "created_at": meta.get("created_at"),
            "algorithm": str(meta.get("algorithm") or ""),
        }

    def get_fingerprint(self) -> str:
        return str(self.get_public_info().get("fingerprint") or "")

    def rotate_key(self) -> dict[str, Any]:
        """Erzeugt neuen Key. Deaktiviert alten Fingerprint für neue Sessions."""
        old_key = _key_path(self._key_dir)
        old_meta = _meta_path(self._key_dir)
        archive_dir = self._key_dir / "rotated"
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        if old_key.exists():
            old_key.rename(archive_dir / f"device_key_{ts}.pem.old")
        if old_meta.exists():
            old_meta.rename(archive_dir / f"device_key_{ts}.json.old")
        return self.generate_key()

    def sign(self, message: bytes) -> bytes:
        """Signiert eine Nachricht mit dem privaten Device-Key."""
        if not _CRYPTO_AVAILABLE:
            raise DeviceKeyError("cryptography library not available")
        key_file = _key_path(self._key_dir)
        if not key_file.exists():
            raise DeviceKeyError("No device key found.")
        try:
            priv_bytes = key_file.read_bytes()
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            priv = load_pem_private_key(priv_bytes, password=None)
            return priv.sign(message)  # type: ignore[attr-defined]
        except Exception as exc:
            raise DeviceKeyError(f"Signing failed: {exc}") from exc


_default_manager: DeviceKeyManager | None = None


def get_device_key_manager(key_dir: Path | None = None) -> DeviceKeyManager:
    global _default_manager
    if key_dir is not None:
        return DeviceKeyManager(key_dir)
    if _default_manager is None:
        _default_manager = DeviceKeyManager()
    return _default_manager
