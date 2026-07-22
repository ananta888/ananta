"""Versioned signing and public trust bootstrap for Hub-owned SFU projections."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping, Protocol

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


ED25519_ALGORITHM = "Ed25519"
LEGACY_HMAC_ALGORITHM = "HMAC-SHA-256"
SIGNATURE_ALGORITHM_VERSION = 1
TRUSTED_KEYSET_SCHEMA = "ananta.sfu-projection-trusted-keyset.v1"
_SIGNING_DOMAIN = b"ananta:sfu-projection:v1:"
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,111}:v[1-9][0-9]{0,8}$")
_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")


class SfuProjectionSigningConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SfuProjectionSignature:
    value: str
    key_id: str
    algorithm: str
    algorithm_version: int
    key_version: int


class SfuProjectionSignerPort(Protocol):
    @property
    def key_id(self) -> str: ...

    @property
    def algorithm(self) -> str: ...

    @property
    def algorithm_version(self) -> int: ...

    @property
    def key_version(self) -> int: ...

    @property
    def public_key_base64url(self) -> str | None: ...

    def sign(self, digest: str) -> SfuProjectionSignature: ...


class SfuProjectionPrivateKeySourcePort(Protocol):
    def load_private_key(self) -> bytearray: ...


class SfuProjectionEd25519KmsAdapterPort(Protocol):
    def sign(self, payload: bytes) -> bytes: ...

    def public_key_bytes(self) -> bytes: ...


class EncodedSecretSfuProjectionPrivateKeySource:
    """Decode a bounded base64url secret supplied by a secret manager."""

    def __init__(self, encoded_secret: str) -> None:
        self._encoded_secret = encoded_secret

    def load_private_key(self) -> bytearray:
        if not 32 <= len(self._encoded_secret) <= 8192 or _B64URL.fullmatch(self._encoded_secret) is None:
            raise SfuProjectionSigningConfigurationError("sfu_projection_private_key_invalid")
        return bytearray(_decode_base64url(self._encoded_secret))


class FileSfuProjectionPrivateKeySource:
    """Read an operator-mounted private key without following links or broad modes."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def load_private_key(self) -> bytearray:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags)
        except OSError as exc:
            raise SfuProjectionSigningConfigurationError("sfu_projection_private_key_unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_mode & 0o077
                or not 0 < metadata.st_size <= 8192
            ):
                raise SfuProjectionSigningConfigurationError("sfu_projection_private_key_permissions_invalid")
            value = bytearray()
            while len(value) <= 8192:
                chunk = os.read(descriptor, min(4096, 8193 - len(value)))
                if not chunk:
                    break
                value.extend(chunk)
            if not value or len(value) > 8192:
                raise SfuProjectionSigningConfigurationError("sfu_projection_private_key_invalid")
            return value
        finally:
            os.close(descriptor)


class Ed25519SfuProjectionSigner:
    def __init__(
        self,
        source: SfuProjectionPrivateKeySourcePort,
        *,
        key_id: str,
        key_version: int,
    ) -> None:
        _validate_key_metadata(key_id, key_version)
        material = source.load_private_key()
        try:
            self._private_key = _load_ed25519_private_key(bytes(material))
        finally:
            material[:] = b"\0" * len(material)
        public = self._private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        self._public_key_base64url = _encode_base64url(public)
        self._key_id = key_id
        self._key_version = key_version

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def algorithm(self) -> str:
        return ED25519_ALGORITHM

    @property
    def algorithm_version(self) -> int:
        return SIGNATURE_ALGORITHM_VERSION

    @property
    def key_version(self) -> int:
        return self._key_version

    @property
    def public_key_base64url(self) -> str:
        return self._public_key_base64url

    def sign(self, digest: str) -> SfuProjectionSignature:
        payload = _signature_payload(digest)
        return SfuProjectionSignature(
            _encode_base64url(self._private_key.sign(payload)),
            self.key_id,
            self.algorithm,
            self.algorithm_version,
            self.key_version,
        )


class KmsEd25519SfuProjectionSigner:
    """Adapter for non-exportable KMS/HSM keys implementing the narrow KMS port."""

    def __init__(
        self,
        adapter: SfuProjectionEd25519KmsAdapterPort,
        *,
        key_id: str,
        key_version: int,
    ) -> None:
        _validate_key_metadata(key_id, key_version)
        if not callable(getattr(adapter, "sign", None)) or not callable(
            getattr(adapter, "public_key_bytes", None)
        ):
            raise SfuProjectionSigningConfigurationError("sfu_projection_kms_adapter_invalid")
        public = bytes(adapter.public_key_bytes())
        if len(public) != 32:
            raise SfuProjectionSigningConfigurationError("sfu_projection_public_key_invalid")
        Ed25519PublicKey.from_public_bytes(public)
        self._adapter = adapter
        self._public_key_base64url = _encode_base64url(public)
        self._key_id = key_id
        self._key_version = key_version

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def algorithm(self) -> str:
        return ED25519_ALGORITHM

    @property
    def algorithm_version(self) -> int:
        return SIGNATURE_ALGORITHM_VERSION

    @property
    def key_version(self) -> int:
        return self._key_version

    @property
    def public_key_base64url(self) -> str:
        return self._public_key_base64url

    def sign(self, digest: str) -> SfuProjectionSignature:
        value = bytes(self._adapter.sign(_signature_payload(digest)))
        if len(value) != 64:
            raise SfuProjectionSigningConfigurationError("sfu_projection_kms_signature_invalid")
        return SfuProjectionSignature(
            _encode_base64url(value),
            self.key_id,
            self.algorithm,
            self.algorithm_version,
            self.key_version,
        )


class HmacSfuProjectionSigner:
    """Explicit unit-test/legacy adapter; never eligible for production bootstrap."""

    def __init__(
        self,
        secret: bytes,
        *,
        key_id: str,
        key_version: int = 1,
        legacy_mode: bool = False,
    ) -> None:
        if legacy_mode is not True:
            raise SfuProjectionSigningConfigurationError("sfu_projection_legacy_hmac_not_explicit")
        if len(secret) < 32 or not key_id or key_version < 1:
            raise SfuProjectionSigningConfigurationError("sfu_projection_signer_invalid")
        self._secret = bytes(secret)
        self._key_id = key_id
        self._key_version = key_version

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def algorithm(self) -> str:
        return LEGACY_HMAC_ALGORITHM

    @property
    def algorithm_version(self) -> int:
        return SIGNATURE_ALGORITHM_VERSION

    @property
    def key_version(self) -> int:
        return self._key_version

    @property
    def public_key_base64url(self) -> None:
        return None

    def sign(self, digest: str) -> SfuProjectionSignature:
        value = hmac.new(self._secret, _signature_payload(digest), hashlib.sha256).digest()
        return SfuProjectionSignature(
            _encode_base64url(value),
            self.key_id,
            self.algorithm,
            self.algorithm_version,
            self.key_version,
        )


TrustedKeyStatus = Literal["active", "overlap", "revoked"]


@dataclass(frozen=True, slots=True)
class SfuProjectionTrustedPublicKey:
    key_id: str
    algorithm: str
    algorithm_version: int
    key_version: int
    status: TrustedKeyStatus
    format: str
    key_material_base64url: str

    def public(self) -> dict[str, object]:
        return {
            "keyId": self.key_id,
            "algorithm": self.algorithm,
            "algorithmVersion": self.algorithm_version,
            "keyVersion": self.key_version,
            "status": self.status,
            "format": self.format,
            "keyMaterialBase64Url": self.key_material_base64url,
        }


@dataclass(frozen=True, slots=True)
class SfuProjectionTrustedKeyset:
    keyset_version: int
    keys: tuple[SfuProjectionTrustedPublicKey, ...]

    @classmethod
    def from_json(cls, raw: str) -> "SfuProjectionTrustedKeyset":
        if not 1 <= len(raw.encode("utf-8")) <= 16_384:
            raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_invalid")
        try:
            document = json.loads(raw, object_pairs_hook=_unique_object)
        except (UnicodeError, json.JSONDecodeError, SfuProjectionSigningConfigurationError) as exc:
            raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_invalid") from exc
        return cls.from_document(document)

    @classmethod
    def from_file(cls, path: str | Path) -> "SfuProjectionTrustedKeyset":
        candidate = Path(path)
        try:
            if candidate.is_symlink() or not candidate.is_file() or not 0 < candidate.stat().st_size <= 16_384:
                raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_unavailable")
            return cls.from_json(candidate.read_text(encoding="utf-8"))
        except OSError as exc:
            raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_unavailable") from exc

    @classmethod
    def from_document(cls, document: object) -> "SfuProjectionTrustedKeyset":
        if not isinstance(document, Mapping) or set(document) != {"schema", "keysetVersion", "keys"}:
            raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_invalid")
        version = document.get("keysetVersion")
        raw_keys = document.get("keys")
        if (
            document.get("schema") != TRUSTED_KEYSET_SCHEMA
            or type(version) is not int
            or version < 1
            or not isinstance(raw_keys, list)
            or not 1 <= len(raw_keys) <= 8
        ):
            raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_invalid")
        keys: list[SfuProjectionTrustedPublicKey] = []
        seen: set[str] = set()
        for raw_key in raw_keys:
            key = _trusted_key(raw_key)
            if key.key_id in seen:
                raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_duplicate")
            seen.add(key.key_id)
            keys.append(key)
        return cls(version, tuple(keys))

    def authorizes(self, signer: SfuProjectionSignerPort) -> bool:
        return any(
            key.key_id == signer.key_id
            and key.algorithm == signer.algorithm
            and key.algorithm_version == signer.algorithm_version
            and key.key_version == signer.key_version
            and key.status == "active"
            and key.key_material_base64url == signer.public_key_base64url
            for key in self.keys
        )

    def public(self) -> dict[str, object]:
        return {
            "schema": TRUSTED_KEYSET_SCHEMA,
            "keysetVersion": self.keyset_version,
            "keys": [key.public() for key in self.keys],
        }


def _trusted_key(value: object) -> SfuProjectionTrustedPublicKey:
    required = {
        "keyId",
        "algorithm",
        "algorithmVersion",
        "keyVersion",
        "status",
        "format",
        "keyMaterialBase64Url",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise SfuProjectionSigningConfigurationError("sfu_projection_key_invalid")
    key_id = value.get("keyId")
    key_version = value.get("keyVersion")
    algorithm_version = value.get("algorithmVersion")
    material = value.get("keyMaterialBase64Url")
    if (
        not isinstance(key_id, str)
        or type(key_version) is not int
        or type(algorithm_version) is not int
        or value.get("algorithm") != ED25519_ALGORITHM
        or algorithm_version != SIGNATURE_ALGORITHM_VERSION
        or value.get("status") not in {"active", "overlap", "revoked"}
        or value.get("format") != "raw"
        or not isinstance(material, str)
    ):
        raise SfuProjectionSigningConfigurationError("sfu_projection_key_invalid")
    _validate_key_metadata(key_id, key_version)
    try:
        public = _decode_base64url(material)
        if len(public) != 32:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(public)
    except ValueError as exc:
        raise SfuProjectionSigningConfigurationError("sfu_projection_public_key_invalid") from exc
    return SfuProjectionTrustedPublicKey(
        key_id,
        ED25519_ALGORITHM,
        SIGNATURE_ALGORITHM_VERSION,
        key_version,
        value["status"],
        "raw",
        material,
    )


def _load_ed25519_private_key(material: bytes) -> Ed25519PrivateKey:
    # Raw Ed25519 private keys are arbitrary 32-byte binary values. Stripping
    # them first corrupts valid keys whose boundary byte happens to be ASCII
    # whitespace. Preserve the exact bytes and add a stripped candidate only
    # for textual PEM/DER material.
    candidates = [material]
    stripped = material.strip()
    if stripped != material:
        candidates.append(stripped)
    try:
        decoded = _decode_base64url(material.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError):
        pass
    else:
        candidates.append(decoded)
    for candidate in candidates:
        try:
            if len(candidate) == 32:
                return Ed25519PrivateKey.from_private_bytes(candidate)
            loader = (
                serialization.load_pem_private_key
                if candidate.startswith(b"-----BEGIN ")
                else serialization.load_der_private_key
            )
            private = loader(candidate, password=None)
            if isinstance(private, Ed25519PrivateKey):
                return private
        except (TypeError, ValueError):
            continue
    raise SfuProjectionSigningConfigurationError("sfu_projection_private_key_invalid")


def _signature_payload(digest: str) -> bytes:
    if _DIGEST.fullmatch(digest) is None:
        raise SfuProjectionSigningConfigurationError("sfu_projection_digest_invalid")
    return _SIGNING_DOMAIN + digest.encode("ascii")


def _validate_key_metadata(key_id: str, key_version: int) -> None:
    if (
        type(key_version) is not int
        or key_version < 1
        or _KEY_ID.fullmatch(key_id) is None
        or not key_id.endswith(f":v{key_version}")
    ):
        raise SfuProjectionSigningConfigurationError("sfu_projection_key_metadata_invalid")


def _decode_base64url(value: str) -> bytes:
    if not value or _B64URL.fullmatch(value) is None:
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SfuProjectionSigningConfigurationError("sfu_projection_keyset_duplicate_property")
        result[key] = value
    return result


__all__ = [
    "ED25519_ALGORITHM",
    "EncodedSecretSfuProjectionPrivateKeySource",
    "Ed25519SfuProjectionSigner",
    "FileSfuProjectionPrivateKeySource",
    "HmacSfuProjectionSigner",
    "KmsEd25519SfuProjectionSigner",
    "LEGACY_HMAC_ALGORITHM",
    "SIGNATURE_ALGORITHM_VERSION",
    "SfuProjectionEd25519KmsAdapterPort",
    "SfuProjectionPrivateKeySourcePort",
    "SfuProjectionSignature",
    "SfuProjectionSignerPort",
    "SfuProjectionSigningConfigurationError",
    "SfuProjectionTrustedKeyset",
    "SfuProjectionTrustedPublicKey",
    "TRUSTED_KEYSET_SCHEMA",
]
