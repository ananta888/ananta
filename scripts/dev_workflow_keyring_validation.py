"""Validation policy for development workflow keyrings."""

from __future__ import annotations

import base64
import binascii
import stat
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from agent.services.source_access_manifest_keyring import SOURCE_ACCESS_KEYRING_SCHEMA
from agent.services.source_access_manifest_signing import SourceAccessSigningKey
from ananta_contracts.runtime_authorization_crypto import Ed25519SigningKeyRing, Ed25519VerificationKeyRing
from scripts.dev_workflow_keyring_contract import (
    _ALL_DOCUMENTS,
    _LEGACY_ALL_DOCUMENTS,
    _UPGRADABLE_WORKER_CAPABILITY_SETS,
    DevWorkflowKeyringBootstrapError,
    WorkerRegistrationSpec,
    _registration_document,
)
from scripts.dev_workflow_keyring_filesystem import _read_json, _read_secret


def _validate(
    paths: dict[str, Path],
    *,
    worker_specs: tuple[WorkerRegistrationSpec, ...],
    allow_legacy_registration: bool = False,
    allow_missing_source_access: bool = False,
) -> bool:
    _assert_credential_modes(
        paths,
        document_names=(_LEGACY_ALL_DOCUMENTS if allow_missing_source_access else _ALL_DOCUMENTS),
    )
    signing = _read_json(paths["signing"], description="development signing keyring")
    verification = _read_json(
        paths["verification"],
        description="development verification keyring",
    )
    dispatch = _read_json(paths["dispatch"], description="development dispatch keyring")

    try:
        signer = Ed25519SigningKeyRing.from_mapping(signing)
        Ed25519VerificationKeyRing.from_mapping(verification)
    except ValueError as exc:
        raise DevWorkflowKeyringBootstrapError("development workflow authorization keyring is invalid") from exc
    if verification != signer.verification_mapping():
        raise DevWorkflowKeyringBootstrapError("development workflow signing and verification keyrings do not match")

    active_key_id = str(dispatch.get("active_key_id") or "")
    keys = dispatch.get("keys")
    if not isinstance(keys, dict) or active_key_id not in keys:
        raise DevWorkflowKeyringBootstrapError("development workflow dispatch keyring is incomplete")
    try:
        Fernet(str(keys[active_key_id]).encode("ascii"))
    except (TypeError, ValueError) as exc:
        raise DevWorkflowKeyringBootstrapError("development workflow dispatch keyring is invalid") from exc

    if not allow_missing_source_access:
        source_access_keyring = _read_json(
            paths["source_access_keyring"],
            description="development source-access keyring",
        )
        _validate_source_access_keyring(
            source_access_keyring,
        )

    secrets_by_name = _read_identity_secrets(paths)
    if len(set(secrets_by_name.values())) != len(secrets_by_name):
        raise DevWorkflowKeyringBootstrapError("development workflow credentials must be disjoint")
    registration = _read_json(
        paths["registration_keyring"],
        description="development Worker registration keyring",
    )
    expected_registration = _registration_document(
        secrets_by_name,
        worker_specs=worker_specs,
    )
    if registration == expected_registration:
        return False
    if allow_legacy_registration:
        for capabilities in _UPGRADABLE_WORKER_CAPABILITY_SETS:
            if registration == _registration_document(
                secrets_by_name,
                worker_specs=worker_specs,
                capabilities=list(capabilities),
            ):
                return True
    raise DevWorkflowKeyringBootstrapError("development Worker registration keyring does not match credentials")


def _read_identity_secrets(
    paths: dict[str, Path],
) -> dict[str, str]:
    secrets_by_name = {
        name: _read_secret(paths[name], description=name)
        for name in (
            "hub_service_token",
            "hub_session_key",
            "alpha_service_token",
            "alpha_registration_token",
            "alpha_session_key",
            "beta_service_token",
            "beta_registration_token",
            "beta_session_key",
        )
    }
    return secrets_by_name


def _validate_source_access_keyring(
    document: dict[str, Any],
) -> None:
    if set(document) != {"schema", "active_key_id", "keys"}:
        raise DevWorkflowKeyringBootstrapError("development source-access keyring is invalid")
    if document.get("schema") != SOURCE_ACCESS_KEYRING_SCHEMA:
        raise DevWorkflowKeyringBootstrapError("development source-access keyring is invalid")
    active_key_id = str(document.get("active_key_id") or "")
    raw_keys = document.get("keys")
    if not isinstance(raw_keys, dict) or active_key_id not in raw_keys:
        raise DevWorkflowKeyringBootstrapError("development source-access keyring is incomplete")
    try:
        for raw_key_id, encoded_secret in raw_keys.items():
            if not isinstance(encoded_secret, str):
                raise ValueError("source-access key material must be text")
            SourceAccessSigningKey(
                key_id=str(raw_key_id),
                secret=base64.b64decode(encoded_secret, validate=True),
            )
    except (ValueError, binascii.Error) as exc:
        raise DevWorkflowKeyringBootstrapError("development source-access keyring is invalid") from exc


def _assert_credential_modes(
    paths: dict[str, Path],
    *,
    document_names: frozenset[str] = _ALL_DOCUMENTS,
) -> None:
    for name in document_names:
        path = paths[name]
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise DevWorkflowKeyringBootstrapError(f"development workflow credential is unavailable: {name}") from exc
        expected_mode = 0o444 if name == "verification" else 0o600
        if (
            not stat.S_ISREG(metadata.st_mode)
            or int(metadata.st_nlink) != 1
            or stat.S_IMODE(metadata.st_mode) != expected_mode
        ):
            raise DevWorkflowKeyringBootstrapError(f"development workflow credential mode is invalid: {name}")
