"""Hostile transfers must fail before any storage or admission side effect."""

import base64
import copy
import hashlib

import pytest

from ananta_contracts.native_artifact_transfer import (
    MAX_ARTIFACT_BYTES,
    MAX_TOTAL_BYTES,
    SCHEMA,
    validate_native_artifact_transfer,
)


@pytest.fixture
def transfer():
    content = b'{"synthetic":true}\n'
    binding = {
        "hub_task_id": "task", "command_digest": "a" * 64, "assignment_id": "assignment",
        "assignment_revision": 1, "attempt_id": "attempt", "fencing_token": 1,
        "dispatch_lease_digest": "b" * 64,
    }
    return {"schema": SCHEMA, "binding": binding, "artifacts": [{
        "output_name": "report", "filename": "report.json", "media_type": "application/json",
        "size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest(),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }]}


def validate(raw, binding):
    return validate_native_artifact_transfer(raw, expected_binding=binding, declared_outputs=("report",))


def test_bytes_survive_transfer_without_worker_storage_identity(transfer):
    artifact, = validate(transfer, transfer["binding"])
    assert artifact.content == b'{"synthetic":true}\n'
    assert artifact.sha256 == hashlib.sha256(artifact.content).hexdigest()


@pytest.mark.parametrize("field,value", [
    ("hub_task_id", "foreign"), ("assignment_id", "foreign"), ("assignment_revision", 2),
    ("attempt_id", "foreign"), ("fencing_token", 2), ("command_digest", "c" * 64),
    ("dispatch_lease_digest", "d" * 64),
])
def test_all_authority_bindings_are_independently_matched(transfer, field, value):
    expected = copy.deepcopy(transfer["binding"])
    transfer["binding"][field] = value
    with pytest.raises(ValueError, match="binding_mismatch"):
        validate(transfer, expected)


@pytest.mark.parametrize("field,value", [
    ("fencing_token", True), ("assignment_revision", 1.0), ("assignment_revision", 0),
    ("dispatch_lease_digest", "RUN_forged"), ("hub_task_id", "task\n"),
])
def test_binding_types_cannot_coerce_even_when_both_sides_match(transfer, field, value):
    transfer["binding"][field] = value
    with pytest.raises(ValueError, match="binding_invalid"):
        validate(transfer, transfer["binding"])


@pytest.mark.parametrize("field,value,reason", [
    ("filename", "../report.json", "filename_invalid"),
    ("filename", "C:\\report.json", "filename_invalid"),
    ("filename", "report\x00.json", "filename_invalid"),
    ("media_type", "application/json\r\nX: value", "media_type_invalid"),
    ("size_bytes", True, "size_invalid"),
    ("size_bytes", MAX_ARTIFACT_BYTES + 1, "size_invalid"),
    ("sha256", "0" * 64, "content_hash_mismatch"),
    ("content_base64", "", "content_size_mismatch"),
    ("output_name", "undeclared", "output_binding_invalid"),
])
def test_hostile_descriptors_fail_closed(transfer, field, value, reason):
    transfer["artifacts"][0][field] = value
    with pytest.raises(ValueError, match=reason):
        validate(transfer, transfer["binding"])


@pytest.mark.parametrize("level", ["transfer", "binding", "artifact"])
def test_worker_cannot_supply_ids_or_receipts(transfer, level):
    target = {"transfer": transfer, "binding": transfer["binding"], "artifact": transfer["artifacts"][0]}[level]
    target["hub_receipt"] = "forged"
    with pytest.raises(ValueError):
        validate(transfer, transfer["binding"])


def test_duplicate_output_is_rejected(transfer):
    transfer["artifacts"].append(copy.deepcopy(transfer["artifacts"][0]))
    with pytest.raises(ValueError, match="output_binding_invalid"):
        validate(transfer, transfer["binding"])


def test_missing_declared_output_is_rejected(transfer):
    with pytest.raises(ValueError, match="output_missing"):
        validate_native_artifact_transfer(
            transfer, expected_binding=transfer["binding"], declared_outputs=("report", "other"),
        )


def test_total_limit_applies_to_actual_decoded_bytes(transfer):
    content = b"x" * MAX_ARTIFACT_BYTES
    item = transfer["artifacts"][0]
    item.update(size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                content_base64=base64.b64encode(content).decode("ascii"))
    transfer["artifacts"] = [{**item, "output_name": str(i)} for i in range(MAX_TOTAL_BYTES // len(content) + 1)]
    with pytest.raises(ValueError, match="total_size_exceeded"):
        validate_native_artifact_transfer(
            transfer, expected_binding=transfer["binding"], declared_outputs=tuple(str(i) for i in range(3)),
        )


def test_noncanonical_base64_padding_bits_are_rejected(transfer):
    transfer["artifacts"][0].update(size_bytes=1, sha256=hashlib.sha256(b"f").hexdigest(), content_base64="Zh==")
    with pytest.raises(ValueError, match="content_size_mismatch"):
        validate(transfer, transfer["binding"])
