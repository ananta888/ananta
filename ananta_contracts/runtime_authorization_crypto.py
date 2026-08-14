"""Asymmetric signing boundary for Hub-issued workflow runtime contracts.

Only the Hub receives :class:`Ed25519SigningKeyRing`. Worker and Temporal
processes receive :class:`Ed25519VerificationKeyRing`, whose configuration
schema rejects private or legacy symmetric key material.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

ED25519_ALGORITHM = "ed25519"
ED25519_SIGNING_KEYRING_SCHEMA = "ananta.workflow-auth-signing-keyring.v1"
ED25519_VERIFICATION_KEYRING_SCHEMA = "ananta.workflow-auth-verification-keyring.v1"
ED25519_PROTECTED_MESSAGE_SCHEMA = "ananta.ed25519-protected-message.v1"


class RuntimeAuthorizationCryptoError(ValueError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code)
        super().__init__(self.reason_code)


class Ed25519VerificationKeyRing:
    """Verify contracts with public keys only; signing is structurally absent."""

    def __init__(
        self,
        public_keys: Mapping[str, str | bytes],
        *,
        revoked_key_ids: tuple[str, ...] = (),
        revoked_contract_ids: tuple[str, ...] = (),
    ) -> None:
        if not public_keys:
            raise RuntimeAuthorizationCryptoError("authorization_verification_keys_required")
        self._keys = _load_public_keys(public_keys)
        self._revoked_keys = {_identifier(value, "authorization_revoked_key_id_invalid") for value in revoked_key_ids}
        self._revoked_contracts = {
            _identifier(value, "authorization_revoked_contract_id_invalid") for value in revoked_contract_ids
        }

    @property
    def signature_algorithm(self) -> str:
        return ED25519_ALGORITHM

    @classmethod
    def from_mapping(
        cls,
        raw: Mapping[str, Any],
    ) -> "Ed25519VerificationKeyRing":
        _assert_exact_fields(
            raw,
            allowed={
                "schema",
                "algorithm",
                "public_keys",
                "revoked_key_ids",
                "revoked_envelope_ids",
            },
        )
        if str(raw.get("schema") or "") != ED25519_VERIFICATION_KEYRING_SCHEMA:
            raise RuntimeAuthorizationCryptoError("authorization_verification_keyring_schema_invalid")
        if str(raw.get("algorithm") or "").lower() != ED25519_ALGORITHM:
            raise RuntimeAuthorizationCryptoError("authorization_verification_algorithm_invalid")
        public_keys = raw.get("public_keys")
        if not isinstance(public_keys, Mapping):
            raise RuntimeAuthorizationCryptoError("authorization_verification_keys_required")
        return cls(
            {str(key): value for key, value in public_keys.items()},
            revoked_key_ids=_identifier_tuple(
                raw.get("revoked_key_ids"),
                "authorization_revoked_key_ids_invalid",
            ),
            revoked_contract_ids=_identifier_tuple(
                raw.get("revoked_envelope_ids"),
                "authorization_revoked_contract_ids_invalid",
            ),
        )

    def verify(
        self,
        *,
        namespace: str,
        payload: dict[str, Any],
        key_id: str,
        signature: str,
        contract_id: str,
        protected_header: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_key_id = _identifier(key_id, "signing_key_unknown")
        if normalized_key_id in self._revoked_keys:
            raise RuntimeAuthorizationCryptoError("signing_key_revoked")
        if str(contract_id) in self._revoked_contracts:
            raise RuntimeAuthorizationCryptoError("signed_contract_revoked")
        key = self._keys.get(normalized_key_id)
        if key is None:
            raise RuntimeAuthorizationCryptoError("signing_key_unknown")
        try:
            raw_signature = base64.b64decode(
                str(signature).encode("ascii"),
                validate=True,
            )
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise RuntimeAuthorizationCryptoError("signature_invalid") from exc
        if len(raw_signature) != 64:
            raise RuntimeAuthorizationCryptoError("signature_invalid")
        try:
            key.verify(
                raw_signature,
                _message(
                    namespace=namespace,
                    payload=payload,
                    protected_header=protected_header,
                ),
            )
        except InvalidSignature as exc:
            raise RuntimeAuthorizationCryptoError("signature_invalid") from exc


class Ed25519SigningKeyRing:
    """Hub-only private signer with a derived public verification view."""

    def __init__(
        self,
        private_keys: Mapping[str, str | bytes],
        *,
        active_key_id: str,
        revoked_key_ids: tuple[str, ...] = (),
        revoked_contract_ids: tuple[str, ...] = (),
    ) -> None:
        if not private_keys:
            raise RuntimeAuthorizationCryptoError("authorization_signing_keys_required")
        self._keys = _load_private_keys(private_keys)
        self._active_key_id = _identifier(
            active_key_id,
            "active_signing_key_missing",
        )
        if self._active_key_id not in self._keys:
            raise RuntimeAuthorizationCryptoError("active_signing_key_missing")
        self._revoked_keys = {_identifier(value, "authorization_revoked_key_id_invalid") for value in revoked_key_ids}
        self._revoked_contracts = {
            _identifier(value, "authorization_revoked_contract_id_invalid") for value in revoked_contract_ids
        }

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "Ed25519SigningKeyRing":
        _assert_exact_fields(
            raw,
            allowed={
                "schema",
                "algorithm",
                "active_key_id",
                "private_keys",
                "public_keys",
                "revoked_key_ids",
                "revoked_envelope_ids",
            },
        )
        if str(raw.get("schema") or "") != ED25519_SIGNING_KEYRING_SCHEMA:
            raise RuntimeAuthorizationCryptoError("authorization_signing_keyring_schema_invalid")
        if str(raw.get("algorithm") or "").lower() != ED25519_ALGORITHM:
            raise RuntimeAuthorizationCryptoError("authorization_signing_algorithm_invalid")
        private_keys = raw.get("private_keys")
        if not isinstance(private_keys, Mapping):
            raise RuntimeAuthorizationCryptoError("authorization_signing_keys_required")
        ring = cls(
            {str(key): value for key, value in private_keys.items()},
            active_key_id=str(raw.get("active_key_id") or ""),
            revoked_key_ids=_identifier_tuple(
                raw.get("revoked_key_ids"),
                "authorization_revoked_key_ids_invalid",
            ),
            revoked_contract_ids=_identifier_tuple(
                raw.get("revoked_envelope_ids"),
                "authorization_revoked_contract_ids_invalid",
            ),
        )
        supplied_public = raw.get("public_keys")
        if supplied_public is not None:
            if not isinstance(supplied_public, Mapping):
                raise RuntimeAuthorizationCryptoError("authorization_public_keys_invalid")
            if ring.public_keys() != {
                str(key): _canonical_key(value, expected_bytes=32, private=False)
                for key, value in supplied_public.items()
            }:
                raise RuntimeAuthorizationCryptoError("authorization_public_private_key_mismatch")
        return ring

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    @property
    def signature_algorithm(self) -> str:
        return ED25519_ALGORITHM

    def sign(
        self,
        *,
        namespace: str,
        payload: dict[str, Any],
        key_id: str | None = None,
        protected_header: Mapping[str, Any] | None = None,
    ) -> tuple[str, str]:
        selected = _identifier(
            key_id or self._active_key_id,
            "signing_key_unknown",
        )
        if selected in self._revoked_keys:
            raise RuntimeAuthorizationCryptoError("active_signing_key_revoked")
        key = self._keys.get(selected)
        if key is None:
            raise RuntimeAuthorizationCryptoError("signing_key_unknown")
        signature = key.sign(
            _message(
                namespace=namespace,
                payload=payload,
                protected_header=protected_header,
            )
        )
        return selected, base64.b64encode(signature).decode("ascii")

    def verify(
        self,
        *,
        namespace: str,
        payload: dict[str, Any],
        key_id: str,
        signature: str,
        contract_id: str,
        protected_header: Mapping[str, Any] | None = None,
    ) -> None:
        self.verification_key_ring().verify(
            namespace=namespace,
            payload=payload,
            key_id=key_id,
            signature=signature,
            contract_id=contract_id,
            protected_header=protected_header,
        )

    def revoke_key(self, key_id: str) -> None:
        self._revoked_keys.add(_identifier(key_id, "authorization_revoked_key_id_invalid"))

    def revoke_contract(self, contract_id: str) -> None:
        self._revoked_contracts.add(_identifier(contract_id, "authorization_revoked_contract_id_invalid"))

    def public_keys(self) -> dict[str, str]:
        return {
            key_id: base64.b64encode(
                key.public_key().public_bytes(
                    encoding=serialization.Encoding.Raw,
                    format=serialization.PublicFormat.Raw,
                )
            ).decode("ascii")
            for key_id, key in self._keys.items()
        }

    def verification_key_ring(self) -> Ed25519VerificationKeyRing:
        return Ed25519VerificationKeyRing(
            self.public_keys(),
            revoked_key_ids=tuple(self._revoked_keys),
            revoked_contract_ids=tuple(self._revoked_contracts),
        )

    def verification_mapping(self) -> dict[str, Any]:
        return {
            "schema": ED25519_VERIFICATION_KEYRING_SCHEMA,
            "algorithm": ED25519_ALGORITHM,
            "public_keys": self.public_keys(),
            "revoked_key_ids": sorted(self._revoked_keys),
            "revoked_envelope_ids": sorted(self._revoked_contracts),
        }


def _assert_exact_fields(raw: Mapping[str, Any], *, allowed: set[str]) -> None:
    unknown = {str(key) for key in raw} - allowed
    if unknown:
        raise RuntimeAuthorizationCryptoError("authorization_keyring_unknown_field")


def _identifier(value: object, reason_code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 256 or "\x00" in normalized:
        raise RuntimeAuthorizationCryptoError(reason_code)
    return normalized


def _identifier_tuple(value: object, reason_code: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise RuntimeAuthorizationCryptoError(reason_code)
    if len(value) > 10_000:
        raise RuntimeAuthorizationCryptoError(reason_code)
    return tuple(_identifier(item, reason_code) for item in value)


def _decode_key(
    value: str | bytes,
    *,
    expected_bytes: int,
    private: bool,
) -> bytes:
    try:
        encoded = value.encode("ascii") if isinstance(value, str) else bytes(value)
        decoded = base64.b64decode(encoded, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        reason = "authorization_private_key_invalid" if private else "authorization_public_key_invalid"
        raise RuntimeAuthorizationCryptoError(reason) from exc
    if len(decoded) != expected_bytes:
        reason = "authorization_private_key_invalid" if private else "authorization_public_key_invalid"
        raise RuntimeAuthorizationCryptoError(reason)
    return decoded


def _canonical_key(
    value: str | bytes,
    *,
    expected_bytes: int,
    private: bool,
) -> str:
    return base64.b64encode(_decode_key(value, expected_bytes=expected_bytes, private=private)).decode("ascii")


def _load_public_keys(
    values: Mapping[str, str | bytes],
) -> dict[str, Ed25519PublicKey]:
    keys: dict[str, Ed25519PublicKey] = {}
    material_owners: dict[bytes, str] = {}
    for raw_key_id, value in values.items():
        key_id = _identifier(raw_key_id, "authorization_key_id_invalid")
        if key_id in keys:
            raise RuntimeAuthorizationCryptoError("authorization_duplicate_key_id")
        material = _decode_key(value, expected_bytes=32, private=False)
        if material in material_owners:
            raise RuntimeAuthorizationCryptoError("authorization_duplicate_key_material")
        material_owners[material] = key_id
        keys[key_id] = Ed25519PublicKey.from_public_bytes(material)
    return keys


def _load_private_keys(
    values: Mapping[str, str | bytes],
) -> dict[str, Ed25519PrivateKey]:
    keys: dict[str, Ed25519PrivateKey] = {}
    material_owners: dict[bytes, str] = {}
    for raw_key_id, value in values.items():
        key_id = _identifier(raw_key_id, "authorization_key_id_invalid")
        if key_id in keys:
            raise RuntimeAuthorizationCryptoError("authorization_duplicate_key_id")
        private_key = Ed25519PrivateKey.from_private_bytes(_decode_key(value, expected_bytes=32, private=True))
        public_material = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if public_material in material_owners:
            raise RuntimeAuthorizationCryptoError("authorization_duplicate_key_material")
        material_owners[public_material] = key_id
        keys[key_id] = private_key
    return keys


def _message(
    *,
    namespace: str,
    payload: dict[str, Any],
    protected_header: Mapping[str, Any] | None = None,
) -> bytes:
    try:
        message: Any = payload
        if protected_header is not None:
            if not isinstance(protected_header, Mapping):
                raise TypeError("protected header must be a mapping")
            message = {
                "schema": ED25519_PROTECTED_MESSAGE_SCHEMA,
                "namespace": str(namespace),
                "protected": dict(protected_header),
                "payload": payload,
            }
        canonical = json.dumps(
            message,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RuntimeAuthorizationCryptoError("authorization_payload_invalid") from exc
    if protected_header is not None:
        return canonical.encode("utf-8")
    return f"{namespace}\n{canonical}".encode("utf-8")


__all__ = [
    "ED25519_ALGORITHM",
    "ED25519_PROTECTED_MESSAGE_SCHEMA",
    "ED25519_SIGNING_KEYRING_SCHEMA",
    "ED25519_VERIFICATION_KEYRING_SCHEMA",
    "Ed25519SigningKeyRing",
    "Ed25519VerificationKeyRing",
    "RuntimeAuthorizationCryptoError",
]
