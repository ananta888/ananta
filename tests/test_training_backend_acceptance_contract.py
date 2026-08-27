from __future__ import annotations

import pytest

from scripts.run_training_backends_acceptance import validate_evidence


def _evidence(result: str = "not_run") -> dict[str, object]:
    return {
        "schema_version": "ananta.training-backend-acceptance.v1",
        "backend": "axolotl",
        "backend_version": "0.18.0",
        "container_digest": None,
        "hardware": {},
        "result": result,
        "tests": [{"name": "local_lora_smoke", "result": result}],
    }


def test_not_run_is_retained_as_an_explicit_non_success_state() -> None:
    validate_evidence(_evidence())


def test_verified_cannot_be_claimed_without_digest_and_hardware_attestation() -> None:
    with pytest.raises(ValueError, match="container digest"):
        validate_evidence(_evidence("verified"))


def test_unknown_result_or_evidence_field_is_rejected() -> None:
    invalid = _evidence()
    invalid["result"] = "passed"
    with pytest.raises(ValueError, match="unsupported backend or result"):
        validate_evidence(invalid)
    unknown = {**_evidence(), "run_id": "RUN_invented"}
    with pytest.raises(ValueError, match="unknown or missing"):
        validate_evidence(unknown)
