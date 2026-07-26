"""Semantic verification for restored credential and Ollama bind trees."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Mapping

from .errors import BackupError
from .filesystem import sha256_file

_MAX_CREDENTIAL_BYTES = 65_536
_MAX_MANIFEST_BYTES = 1024 * 1024
_OLLAMA_DIGEST = re.compile(r"^sha256:([a-f0-9]{64})$")
_ED25519_ALGORITHM = "ed25519"
_SIGNING_SCHEMA = "ananta.workflow-auth-signing-keyring.v1"
_VERIFICATION_SCHEMA = "ananta.workflow-auth-verification-keyring.v1"
_SIGNING_FIELDS = {
    "schema",
    "algorithm",
    "active_key_id",
    "private_keys",
    "public_keys",
    "revoked_key_ids",
    "revoked_envelope_ids",
}
_VERIFICATION_FIELDS = {
    "schema",
    "algorithm",
    "public_keys",
    "revoked_key_ids",
    "revoked_envelope_ids",
}


class WorkflowCredentialVerifier:
    """Validate the complete development Hub/Worker credential relationship."""

    _FILES = {
        "hub_service": "hub/hub-service-token",
        "hub_session": "hub/hub-session-signing-key",
        "signing": "hub/workflow-auth-signing-keyring.json",
        "dispatch": "hub/workflow-dispatch-keyring.json",
        "registration": "hub/worker-registration-keyring.json",
        "verification": "worker/workflow-auth-verification-keyring.json",
        "alpha_registration": "alpha/worker-registration-token",
        "alpha_service": "alpha/worker-service-token",
        "alpha_session": "alpha/worker-session-signing-key",
        "beta_registration": "beta/worker-registration-token",
        "beta_service": "beta/worker-service-token",
        "beta_session": "beta/worker-session-signing-key",
    }

    def verify(self, root: Path) -> None:
        raw = {
            label: self._read_credential_file(
                root / relative,
                label,
                expected_modes=(
                    {0o400, 0o444}
                    if label == "verification"
                    else {0o600}
                ),
            )
            for label, relative in self._FILES.items()
        }
        tokens = {
            label: self._token(raw[label], label)
            for label in (
                "hub_service",
                "hub_session",
                "alpha_registration",
                "alpha_service",
                "alpha_session",
                "beta_registration",
                "beta_service",
                "beta_session",
            )
        }
        self._require_distinct(tokens)
        signing = self._json_object(raw["signing"], "signing keyring")
        verification = self._json_object(
            raw["verification"],
            "verification keyring",
        )
        self._verify_authorization_keyrings(signing, verification)
        self._verify_dispatch(raw["dispatch"])
        self._verify_registration(raw["registration"], tokens)

    @staticmethod
    def _read_credential_file(
        path: Path,
        label: str,
        *,
        expected_modes: set[int],
    ) -> bytes:
        try:
            metadata = path.lstat()
            mode = stat.S_IMODE(metadata.st_mode)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or mode not in expected_modes
                or metadata.st_size < 1
                or metadata.st_size > _MAX_CREDENTIAL_BYTES
            ):
                raise BackupError(
                    f"Restored credential has unsafe metadata: {label}"
                )
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            with os.fdopen(descriptor, "rb") as source:
                opened = os.fstat(source.fileno())
                if (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                ) != (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                ):
                    raise BackupError(
                        f"Restored credential changed during verification: {label}"
                    )
                payload = source.read(_MAX_CREDENTIAL_BYTES + 1)
        except OSError as exc:
            raise BackupError(
                f"Cannot verify restored credential: {label}"
            ) from exc
        if len(payload) > _MAX_CREDENTIAL_BYTES:
            raise BackupError(f"Restored credential is too large: {label}")
        return payload

    @staticmethod
    def _token(payload: bytes, label: str) -> str:
        try:
            token = payload.decode("utf-8")
        except UnicodeError as exc:
            raise BackupError(
                f"Restored credential is not UTF-8: {label}"
            ) from exc
        if token.endswith("\r\n"):
            normalized = token[:-2]
        elif token.endswith("\n"):
            normalized = token[:-1]
        else:
            normalized = token
        if (
            normalized != normalized.strip()
            or len(normalized.encode("utf-8")) < 32
            or "\x00" in normalized
            or any(character.isspace() for character in normalized)
        ):
            raise BackupError(f"Restored credential token is invalid: {label}")
        return normalized

    @staticmethod
    def _require_distinct(tokens: Mapping[str, str]) -> None:
        values = list(tokens.items())
        for index, (left_label, left) in enumerate(values):
            for right_label, right in values[index + 1 :]:
                if secrets.compare_digest(left, right):
                    raise BackupError(
                        "Restored credentials reuse secret material: "
                        f"{left_label}/{right_label}"
                    )

    @staticmethod
    def _json_object(payload: bytes, label: str) -> dict[str, object]:
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BackupError(f"Restored {label} is invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise BackupError(f"Restored {label} must be an object")
        return decoded

    @classmethod
    def _verify_authorization_keyrings(
        cls,
        signing: dict[str, object],
        verification: dict[str, object],
    ) -> None:
        cls._require_exact_fields(signing, _SIGNING_FIELDS, "signing keyring")
        cls._require_exact_fields(
            verification,
            _VERIFICATION_FIELDS,
            "verification keyring",
        )
        if (
            signing.get("schema") != _SIGNING_SCHEMA
            or signing.get("algorithm") != _ED25519_ALGORITHM
        ):
            raise BackupError("Restored signing keyring schema is invalid")
        if (
            verification.get("schema") != _VERIFICATION_SCHEMA
            or verification.get("algorithm") != _ED25519_ALGORITHM
        ):
            raise BackupError("Restored verification keyring schema is invalid")

        private_keys = cls._key_mapping(
            signing.get("private_keys"),
            "private",
        )
        public_keys = cls._key_mapping(
            verification.get("public_keys"),
            "public",
        )
        active_key_id = cls._identifier(
            signing.get("active_key_id"),
            "active signing key",
        )
        if active_key_id not in private_keys:
            raise BackupError("Restored active signing key is missing")
        if set(private_keys) != set(public_keys):
            raise BackupError(
                "Restored signing and verification key identifiers do not match"
            )
        derived_public = {
            key_id: cls._derive_ed25519_public(encoded_private)
            for key_id, encoded_private in private_keys.items()
        }
        if derived_public != public_keys:
            raise BackupError(
                "Restored signing private keys do not match verification public keys"
            )

        signing_public = signing.get("public_keys")
        if signing_public is not None:
            supplied_public = cls._key_mapping(
                signing_public,
                "public",
            )
            if supplied_public != public_keys:
                raise BackupError(
                    "Restored signing and verification public keys do not match"
                )

        signing_revoked_keys = cls._identifier_list(
            signing.get("revoked_key_ids"),
            "revoked signing key",
        )
        verification_revoked_keys = cls._identifier_list(
            verification.get("revoked_key_ids"),
            "revoked verification key",
        )
        signing_revoked_envelopes = cls._identifier_list(
            signing.get("revoked_envelope_ids"),
            "revoked signing envelope",
        )
        verification_revoked_envelopes = cls._identifier_list(
            verification.get("revoked_envelope_ids"),
            "revoked verification envelope",
        )
        if (
            signing_revoked_keys != verification_revoked_keys
            or signing_revoked_envelopes != verification_revoked_envelopes
        ):
            raise BackupError(
                "Restored signing and verification revocations do not match"
            )
        if active_key_id in signing_revoked_keys:
            raise BackupError("Restored active signing key is revoked")

    @staticmethod
    def _require_exact_fields(
        payload: Mapping[str, object],
        allowed: set[str],
        label: str,
    ) -> None:
        unknown = {str(key) for key in payload} - allowed
        if unknown:
            raise BackupError(f"Restored {label} contains unknown fields")

    @classmethod
    def _key_mapping(
        cls,
        value: object,
        key_kind: str,
    ) -> dict[str, str]:
        if not isinstance(value, dict) or not value or len(value) > 10_000:
            raise BackupError(
                f"Restored {key_kind} authorization keys are invalid"
            )
        normalized: dict[str, str] = {}
        for key_id, encoded in value.items():
            normalized_id = cls._identifier(
                key_id,
                f"{key_kind} authorization key",
            )
            if not isinstance(encoded, str):
                raise BackupError(
                    f"Restored {key_kind} authorization key is invalid"
                )
            raw = cls._decode_base64(
                encoded,
                f"{key_kind} authorization key",
            )
            if len(raw) != 32:
                raise BackupError(
                    f"Restored {key_kind} authorization key is invalid"
                )
            normalized[normalized_id] = base64.b64encode(raw).decode("ascii")
        return normalized

    @classmethod
    def _identifier_list(
        cls,
        value: object,
        label: str,
    ) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or len(value) > 10_000:
            raise BackupError(f"Restored {label} list is invalid")
        normalized = tuple(cls._identifier(item, label) for item in value)
        if len(set(normalized)) != len(normalized):
            raise BackupError(f"Restored {label} list contains duplicates")
        return tuple(sorted(normalized))

    @staticmethod
    def _identifier(value: object, label: str) -> str:
        if not isinstance(value, str):
            raise BackupError(f"Restored {label} identifier is invalid")
        normalized = value.strip()
        if (
            not normalized
            or normalized != value
            or len(normalized) > 256
            or "\x00" in normalized
        ):
            raise BackupError(f"Restored {label} identifier is invalid")
        return normalized

    @staticmethod
    def _decode_base64(value: str, label: str) -> bytes:
        try:
            return base64.b64decode(value.encode("ascii"), validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise BackupError(f"Restored {label} is invalid") from exc

    @classmethod
    def _derive_ed25519_public(cls, encoded_private: str) -> str:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey,
            )
        except ImportError as exc:
            raise BackupError(
                "Restore credential verification requires the Python "
                "'cryptography' package"
            ) from exc
        private_seed = cls._decode_base64(
            encoded_private,
            "private authorization key",
        )
        try:
            public = Ed25519PrivateKey.from_private_bytes(
                private_seed
            ).public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
        except ValueError as exc:
            raise BackupError(
                "Restored private authorization key is invalid"
            ) from exc
        return base64.b64encode(public).decode("ascii")

    def _verify_dispatch(self, payload: bytes) -> None:
        decoded = self._json_object(payload, "dispatch keyring")
        if set(decoded) != {"active_key_id", "keys"}:
            raise BackupError("Restored dispatch keyring contains unknown fields")
        keys = decoded.get("keys")
        active_key_id = decoded.get("active_key_id")
        if (
            not isinstance(keys, dict)
            or not keys
            or len(keys) > 10_000
            or not isinstance(active_key_id, str)
            or active_key_id not in keys
        ):
            raise BackupError("Restored dispatch keyring is incomplete")
        try:
            self._identifier(active_key_id, "active dispatch key")
            for key_id, value in keys.items():
                self._identifier(key_id, "dispatch key")
                if not isinstance(value, str):
                    raise ValueError
                decoded_key = base64.b64decode(
                    value.encode("ascii"),
                    altchars=b"-_",
                    validate=True,
                )
                if (
                    len(decoded_key) != 32
                    or base64.urlsafe_b64encode(decoded_key).decode("ascii")
                    != value
                ):
                    raise ValueError
        except (UnicodeError, binascii.Error, ValueError) as exc:
            raise BackupError("Restored dispatch keyring contains an invalid key") from exc

    def _verify_registration(
        self,
        payload: bytes,
        tokens: Mapping[str, str],
    ) -> None:
        decoded = self._json_object(payload, "worker registration keyring")
        workers = decoded.get("workers")
        if (
            decoded.get("schema")
            != "ananta.workflow-worker-registration-keyring.v1"
            or not isinstance(workers, dict)
            or len(workers) < 2
        ):
            raise BackupError("Restored worker registration keyring is invalid")
        matched_workers: set[str] = set()
        for logical_name in ("alpha", "beta"):
            service_digest = hashlib.sha256(
                tokens[f"{logical_name}_service"].encode("utf-8")
            ).hexdigest()
            session_digest = hashlib.sha256(
                tokens[f"{logical_name}_session"].encode("utf-8")
            ).hexdigest()
            matches = [
                str(worker_id)
                for worker_id, entry in workers.items()
                if isinstance(entry, dict)
                and secrets.compare_digest(
                    str(entry.get("registration_token") or ""),
                    tokens[f"{logical_name}_registration"],
                )
                and secrets.compare_digest(
                    str(entry.get("service_token_sha256") or ""),
                    service_digest,
                )
                and secrets.compare_digest(
                    str(entry.get("session_signing_key_sha256") or ""),
                    session_digest,
                )
                and isinstance(entry.get("worker_url"), str)
                and isinstance(entry.get("allowed_capabilities"), list)
                and bool(entry.get("allowed_capabilities"))
            ]
            if len(matches) != 1 or matches[0] in matched_workers:
                raise BackupError(
                    f"Restored {logical_name} credential binding is invalid"
                )
            matched_workers.add(matches[0])


class OllamaModelVerifier:
    """Verify Ollama manifests and every content-addressed referenced blob."""

    def verify(self, models_root: Path) -> None:
        manifests_root = models_root / "manifests"
        blobs_root = models_root / "blobs"
        if (
            not manifests_root.is_dir()
            or manifests_root.is_symlink()
            or not blobs_root.is_dir()
            or blobs_root.is_symlink()
        ):
            raise BackupError("Restored Ollama model layout is incomplete")
        manifests = sorted(
            path
            for path in manifests_root.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
        if not manifests:
            raise BackupError("Restored Ollama model tree has no manifests")
        expected_blobs: dict[str, int] = {}
        for manifest in manifests:
            payload = self._load_manifest(manifest)
            descriptors = [payload.get("config"), *payload.get("layers", [])]
            if not descriptors:
                raise BackupError(
                    f"Ollama manifest has no blob descriptors: {manifest.name}"
                )
            for descriptor in descriptors:
                if not isinstance(descriptor, dict):
                    raise BackupError(
                        f"Ollama manifest descriptor is invalid: {manifest.name}"
                    )
                digest = descriptor.get("digest")
                match = (
                    _OLLAMA_DIGEST.fullmatch(digest)
                    if isinstance(digest, str)
                    else None
                )
                if match is None:
                    raise BackupError(
                        f"Ollama manifest digest is invalid: {manifest.name}"
                    )
                size = descriptor.get("size")
                if (
                    not isinstance(size, int)
                    or isinstance(size, bool)
                    or size < 0
                ):
                    raise BackupError(
                        f"Ollama manifest blob size is invalid: {manifest.name}"
                    )
                normalized_digest = match.group(1)
                previous_size = expected_blobs.get(normalized_digest)
                if previous_size is not None and previous_size != size:
                    raise BackupError(
                        "Ollama manifests disagree about blob size: "
                        f"{normalized_digest}"
                    )
                expected_blobs[normalized_digest] = size
        for digest, expected_size in sorted(expected_blobs.items()):
            blob = blobs_root / f"sha256-{digest}"
            actual, actual_size = sha256_file(blob)
            if not secrets.compare_digest(actual, digest):
                raise BackupError(
                    f"Restored Ollama blob digest does not match: {digest}"
                )
            if actual_size != expected_size:
                raise BackupError(
                    f"Restored Ollama blob size does not match: {digest}"
                )

    @staticmethod
    def _load_manifest(path: Path) -> dict[str, object]:
        try:
            metadata = path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size < 2
                or metadata.st_size > _MAX_MANIFEST_BYTES
            ):
                raise BackupError(
                    f"Restored Ollama manifest has unsafe metadata: {path.name}"
                )
            decoded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BackupError(
                f"Cannot read restored Ollama manifest: {path.name}"
            ) from exc
        if (
            not isinstance(decoded, dict)
            or decoded.get("schemaVersion") != 2
            or not isinstance(decoded.get("layers"), list)
        ):
            raise BackupError(f"Restored Ollama manifest is invalid: {path.name}")
        return decoded


class RestoredBindVerifier:
    """Facade for the two independent restored bind-tree verifiers."""

    def __init__(
        self,
        workflow: WorkflowCredentialVerifier | None = None,
        ollama: OllamaModelVerifier | None = None,
    ) -> None:
        self.workflow = workflow or WorkflowCredentialVerifier()
        self.ollama = ollama or OllamaModelVerifier()

    def verify(self, bind_trees: Mapping[str, Path]) -> bool:
        self.workflow.verify(bind_trees["workflow_secrets"])
        ollama_root = bind_trees.get("ollama_models")
        if ollama_root is None:
            return False
        self.ollama.verify(ollama_root)
        return True
