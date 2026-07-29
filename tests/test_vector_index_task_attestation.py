from __future__ import annotations

import json
import stat

import pytest

from agent.services.vector_index_task_attestation_service import (
    VectorIndexTaskSigningConfigurationError,
    load_vector_index_task_signer,
)
from ananta_contracts.runtime_authorization_crypto import (
    Ed25519VerificationKeyRing,
    RuntimeAuthorizationCryptoError,
)
from ananta_contracts.vector_index_task_attestation import (
    VECTOR_INDEX_TASK_ATTESTATION_SCHEMA,
    VectorIndexTaskAttestationError,
    VectorIndexTaskVerifier,
)
from scripts.generate_vector_index_task_keyrings import generate
from tests.vector_index_attestation_test_support import (
    SIGNING_KEYRING_MAPPING,
    TASK_SIGNER,
    VERIFICATION_KEYRING_MAPPING,
)
from worker.retrieval.vector_index_task_verification import (
    UnavailableVectorIndexTaskVerifier,
    load_vector_index_task_verifier,
)


def _write_keyring(path, payload: dict, *, mode: int = 0o600) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True),
        encoding="utf-8",
    )
    path.chmod(mode)


def _envelope() -> dict:
    return {
        "schema": "ananta.vector_index_task.v1",
        "job_id": "vector-index-1234567890abcdef",
        "operation": "index",
        "payload": {},
    }


def test_hub_private_signer_and_worker_public_verifier_are_separate(
    tmp_path,
) -> None:
    signing_path = tmp_path / "signing.json"
    verification_path = tmp_path / "verification.json"
    _write_keyring(signing_path, SIGNING_KEYRING_MAPPING)
    _write_keyring(verification_path, VERIFICATION_KEYRING_MAPPING)

    signer = load_vector_index_task_signer({"ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE": str(signing_path)})
    verifier = load_vector_index_task_verifier(
        {"ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE": str(verification_path)}
    )
    signed = signer.attest(_envelope())

    verifier.verify(signed)
    assert signed["hub_attestation"]["algorithm"] == "ed25519"
    assert (
        signed["hub_attestation"]["schema"]
        == VECTOR_INDEX_TASK_ATTESTATION_SCHEMA
    )
    assert not hasattr(signer, "verify")
    assert not hasattr(verifier, "attest")


def test_worker_rejects_private_signing_keyring(tmp_path) -> None:
    signing_path = tmp_path / "signing.json"
    _write_keyring(signing_path, SIGNING_KEYRING_MAPPING)

    with pytest.raises(
        ValueError,
        match="vector_index_task_verification_keyring_invalid",
    ):
        load_vector_index_task_verifier({"ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE": str(signing_path)})


def test_hub_accepts_owner_read_only_private_keyring(tmp_path) -> None:
    signing_path = tmp_path / "signing.json"
    _write_keyring(signing_path, SIGNING_KEYRING_MAPPING, mode=0o400)

    signer = load_vector_index_task_signer(
        {
            "ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE": str(
                signing_path
            )
        }
    )

    assert signer.attest(_envelope())["hub_attestation"]["signature"]


@pytest.mark.parametrize("mode", (0o640, 0o644, 0o444))
def test_hub_rejects_group_or_world_readable_private_keyring(
    tmp_path,
    mode: int,
) -> None:
    signing_path = tmp_path / "signing.json"
    _write_keyring(signing_path, SIGNING_KEYRING_MAPPING, mode=mode)

    with pytest.raises(
        VectorIndexTaskSigningConfigurationError,
        match="vector_index_task_signing_keyring_unsafe",
    ):
        load_vector_index_task_signer(
            {
                "ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE": str(
                    signing_path
                )
            }
        )


@pytest.mark.parametrize("mode", (0o444, 0o644))
def test_worker_accepts_readable_public_verification_keyring(
    tmp_path,
    mode: int,
) -> None:
    verification_path = tmp_path / "verification.json"
    _write_keyring(
        verification_path,
        VERIFICATION_KEYRING_MAPPING,
        mode=mode,
    )

    verifier = load_vector_index_task_verifier(
        {
            "ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE": str(
                verification_path
            )
        }
    )
    verifier.verify(TASK_SIGNER.attest(_envelope()))


def test_missing_keyrings_fail_closed() -> None:
    with pytest.raises(
        VectorIndexTaskSigningConfigurationError,
        match="vector_index_task_signing_keyring_required",
    ):
        load_vector_index_task_signer({})

    verifier = load_vector_index_task_verifier({})
    assert isinstance(verifier, UnavailableVectorIndexTaskVerifier)
    with pytest.raises(
        ValueError,
        match="vector_index_task_verification_keyring_required",
    ):
        verifier.verify(_envelope())


def test_tampered_signed_envelope_is_rejected(tmp_path) -> None:
    signing_path = tmp_path / "signing.json"
    verification_path = tmp_path / "verification.json"
    _write_keyring(signing_path, SIGNING_KEYRING_MAPPING)
    _write_keyring(verification_path, VERIFICATION_KEYRING_MAPPING)
    signer = load_vector_index_task_signer({"ANANTA_VECTOR_INDEX_TASK_SIGNING_KEYRING_FILE": str(signing_path)})
    verifier = load_vector_index_task_verifier(
        {"ANANTA_VECTOR_INDEX_TASK_VERIFICATION_KEYRING_FILE": str(verification_path)}
    )
    signed = signer.attest(_envelope())
    signed["operation"] = "delete"

    with pytest.raises(
        ValueError,
        match="vector_index_task_attestation_invalid",
    ):
        verifier.verify(signed)


def test_attestation_rejects_key_id_relabeling_with_same_public_key() -> None:
    signed = TASK_SIGNER.attest(_envelope())
    original_key_id = str(signed["hub_attestation"]["key_id"])
    public_value = VERIFICATION_KEYRING_MAPPING["public_keys"][
        original_key_id
    ]
    alias_key_id = "unrevoked-key-alias"
    alias_ring = Ed25519VerificationKeyRing.from_mapping(
        {
            **VERIFICATION_KEYRING_MAPPING,
            "public_keys": {alias_key_id: public_value},
            "revoked_key_ids": [original_key_id],
        }
    )
    signed["hub_attestation"]["key_id"] = alias_key_id

    with pytest.raises(
        VectorIndexTaskAttestationError,
        match="vector_index_task_attestation_invalid",
    ):
        VectorIndexTaskVerifier(alias_ring).verify(signed)


def test_verification_keyring_rejects_duplicate_public_key_material() -> None:
    original_key_id = next(
        iter(VERIFICATION_KEYRING_MAPPING["public_keys"])
    )
    public_value = VERIFICATION_KEYRING_MAPPING["public_keys"][
        original_key_id
    ]

    with pytest.raises(
        RuntimeAuthorizationCryptoError,
        match="authorization_duplicate_key_material",
    ):
        Ed25519VerificationKeyRing.from_mapping(
            {
                **VERIFICATION_KEYRING_MAPPING,
                "public_keys": {
                    original_key_id: public_value,
                    "duplicate-alias": public_value,
                },
            }
        )


def test_keyring_generator_separates_private_and_public_material(
    tmp_path,
) -> None:
    signing_path, verification_path = generate(
        output_dir=tmp_path / "secrets",
        key_id="rotation-v1",
    )
    signing = json.loads(signing_path.read_text(encoding="utf-8"))
    verification = json.loads(verification_path.read_text(encoding="utf-8"))

    assert "private_keys" in signing
    assert "private_keys" not in verification
    assert signing["public_keys"] == verification["public_keys"]
    assert stat.S_IMODE(signing_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(verification_path.stat().st_mode) == 0o644
    with pytest.raises(FileExistsError):
        generate(
            output_dir=tmp_path / "secrets",
            key_id="rotation-v2",
        )
