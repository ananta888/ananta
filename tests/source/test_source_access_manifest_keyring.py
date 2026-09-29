from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from agent.services.source_access_manifest_keyring import (
    KNOWLEDGE_INDEX_WORKER_ID_ENV,
    SOURCE_ACCESS_COMPOSE_DERIVATION_ENV,
    SOURCE_ACCESS_KEYRING_FILE_ENV,
    SOURCE_ACCESS_KEYRING_SCHEMA,
    SourceAccessManifestKeyringError,
    load_source_access_manifest_keyring,
    resolve_knowledge_index_worker_id,
)
from agent.services.source_access_manifest_signing import (
    HubSourceAccessManifestSigner,
    WorkerSourceAccessManifestVerifier,
)


def test_production_keyring_loads_active_and_rotated_verification_keys(
    tmp_path: Path,
) -> None:
    path = tmp_path / "source-access-keyring.json"
    path.write_text(
        json.dumps(
            {
                "schema": SOURCE_ACCESS_KEYRING_SCHEMA,
                "active_key_id": "current",
                "keys": {
                    "previous": base64.b64encode(b"p" * 32).decode("ascii"),
                    "current": base64.b64encode(b"c" * 32).decode("ascii"),
                },
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    keyring = load_source_access_manifest_keyring(
        {SOURCE_ACCESS_KEYRING_FILE_ENV: str(path)}
    )

    assert keyring.active_signing_key.key_id == "current"
    assert set(keyring.verification_keys) == {"previous", "current"}
    assert keyring.local_compose_derived is False


def test_missing_production_keyring_fails_closed() -> None:
    with pytest.raises(SourceAccessManifestKeyringError) as error:
        load_source_access_manifest_keyring({})

    assert error.value.reason_code == "source_access_keyring_required"


def test_explicit_compose_safe_derivation_uses_existing_secret() -> None:
    environment = {
        SOURCE_ACCESS_COMPOSE_DERIVATION_ENV: "1",
        "ANANTA_RUNTIME_PROFILE": "compose-safe",
        "ANANTA_QUICKSTART_MODE": "role",
        "SECRET_KEY": "local-compose-secret-with-at-least-32-bytes",
        KNOWLEDGE_INDEX_WORKER_ID_ENV: "worker-alpha",
    }

    keyring = load_source_access_manifest_keyring(environment)
    digest = "a" * 64
    signature = HubSourceAccessManifestSigner(
        keyring.active_signing_key
    ).sign(manifest_digest=digest)

    assert keyring.local_compose_derived is True
    assert WorkerSourceAccessManifestVerifier(
        keyring.verification_keys
    ).verify(manifest_digest=digest, signature=signature)
    assert resolve_knowledge_index_worker_id(environment) == "worker-alpha"


def test_compose_derivation_is_unavailable_outside_compose_safe() -> None:
    with pytest.raises(SourceAccessManifestKeyringError) as error:
        load_source_access_manifest_keyring(
            {
                SOURCE_ACCESS_COMPOSE_DERIVATION_ENV: "1",
                "ANANTA_RUNTIME_PROFILE": "production",
                "ANANTA_QUICKSTART_MODE": "role",
                "SECRET_KEY": "production-secret-with-at-least-32-bytes",
            }
        )

    assert error.value.reason_code == "source_access_keyring_required"
