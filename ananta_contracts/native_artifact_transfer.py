"""Bounded byte transfer for Native outputs; validation is not admission.

The Hub must obtain the expected binding and declared outputs from its current
Task/assignment authority. Digests bind transport content, never evidence IDs.
No worker paths, database IDs, receipts or authority decisions are accepted.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass

from ananta_contracts.native_context_bundle import native_context_digest

SCHEMA = "ananta.native-artifact-transfer.v1"
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_ARTIFACT_COUNT = 32
MAX_TRANSFER_HTTP_BYTES = 4 * ((MAX_TOTAL_BYTES + 2) // 3) + 262_144
MAX_RECEIPT_HTTP_BYTES = 262_144
MAX_INPUT_HTTP_BYTES = 4 * ((MAX_ARTIFACT_BYTES + 2) // 3) + 262_144
_DIGEST = re.compile(r"[0-9a-f]{64}")
_MEDIA = re.compile(r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+")
_BINDING_FIELDS = frozenset({
    "hub_task_id", "command_digest", "assignment_id", "assignment_revision",
    "attempt_id", "fencing_token", "dispatch_lease_digest",
})
_ITEM_FIELDS = frozenset({"output_name", "filename", "media_type", "size_bytes", "sha256", "content_base64"})


@dataclass(frozen=True, slots=True)
class NativeArtifactBytes:
    output_name: str
    filename: str
    media_type: str
    content: bytes
    sha256: str


def validate_native_artifact_transfer(
    raw: object, *, expected_binding: Mapping[str, object], declared_outputs: tuple[str, ...],
) -> tuple[NativeArtifactBytes, ...]:
    """Decode a closed transfer only against independently supplied Hub facts."""
    _validate_binding(expected_binding)
    if not isinstance(raw, Mapping) or set(raw) != {"schema", "binding", "artifacts"} or raw["schema"] != SCHEMA:
        raise ValueError("native_artifact_transfer_invalid")
    binding = raw["binding"]
    _validate_binding(binding)
    if dict(binding) != dict(expected_binding):
        raise ValueError("native_artifact_transfer_binding_mismatch")
    items = raw["artifacts"]
    if type(items) is not list or not 1 <= len(items) <= MAX_ARTIFACT_COUNT:
        raise ValueError("native_artifact_transfer_count_invalid")
    if len(set(declared_outputs)) != len(declared_outputs):
        raise ValueError("native_artifact_output_contract_invalid")
    decoded = []
    names = set()
    total = 0
    for item in items:
        if not isinstance(item, Mapping) or set(item) != _ITEM_FIELDS:
            raise ValueError("native_artifact_descriptor_invalid")
        name = item["output_name"]
        if not isinstance(name, str) or name not in declared_outputs or name in names:
            raise ValueError("native_artifact_output_binding_invalid")
        names.add(name)
        filename = item["filename"]
        if (
            not isinstance(filename, str) or not filename or len(filename.encode("utf-8")) > 255
            or filename in {".", ".."} or filename != filename.strip()
            or any(c in filename for c in "/\\") or any(ord(c) < 32 or ord(c) == 127 for c in filename)
        ):
            raise ValueError("native_artifact_filename_invalid")
        media = item["media_type"]
        if not isinstance(media, str) or len(media) > 127 or _MEDIA.fullmatch(media) is None:
            raise ValueError("native_artifact_media_type_invalid")
        size = item["size_bytes"]
        if type(size) is not int or not 0 <= size <= MAX_ARTIFACT_BYTES:
            raise ValueError("native_artifact_size_invalid")
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ValueError("native_artifact_total_size_exceeded")
        digest = item["sha256"]
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise ValueError("native_artifact_digest_invalid")
        encoded = item["content_base64"]
        if not isinstance(encoded, str) or len(encoded) != 4 * ((size + 2) // 3):
            raise ValueError("native_artifact_content_size_mismatch")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("native_artifact_content_encoding_invalid") from exc
        if len(content) != size or base64.b64encode(content).decode("ascii") != encoded:
            raise ValueError("native_artifact_content_size_mismatch")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("native_artifact_content_hash_mismatch")
        decoded.append(NativeArtifactBytes(name, filename, media, content, digest))
    if names != set(declared_outputs):
        raise ValueError("native_artifact_output_missing")
    return tuple(decoded)


def _validate_binding(raw: object) -> None:
    if not isinstance(raw, Mapping) or set(raw) != _BINDING_FIELDS:
        raise ValueError("native_artifact_transfer_binding_invalid")
    for key in ("assignment_revision", "fencing_token"):
        if type(raw[key]) is not int or raw[key] < 1:
            raise ValueError("native_artifact_transfer_binding_invalid")
    for key in ("command_digest", "dispatch_lease_digest"):
        if not isinstance(raw[key], str) or _DIGEST.fullmatch(raw[key]) is None:
            raise ValueError("native_artifact_transfer_binding_invalid")
    for key in ("hub_task_id", "assignment_id", "attempt_id"):
        value = raw[key]
        if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 33 for c in value):
            raise ValueError("native_artifact_transfer_binding_invalid")


def validate_native_artifact_receipts(
    raw: object, *, transfer: Mapping, tenant_id: str, project_id: str | None,
) -> dict[str, str]:
    """Accept only exact Hub receipts for the independently prepared byte transfer."""
    binding = transfer.get("binding")
    items = transfer.get("artifacts")
    if type(items) is not list:
        raise ValueError("native_artifact_transfer_invalid")
    candidates = validate_native_artifact_transfer(
        transfer, expected_binding=binding,
        declared_outputs=tuple(item.get("output_name") for item in items if isinstance(item, Mapping)),
    )
    if (
        not isinstance(raw, Mapping) or set(raw) != {"schema", "artifacts"}
        or raw["schema"] != "ananta.native-artifact-receipts.v1" or type(raw["artifacts"]) is not list
        or len(raw["artifacts"]) != len(candidates)
    ):
        raise ValueError("native_artifact_receipts_invalid")
    expected_items = {item.output_name: item for item in candidates}
    references = {}
    for receipt in raw["artifacts"]:
        fields = {
            "schema", "classification", "binding", "tenant_id", "project_id", "output_name",
            "filename", "media_type", "size_bytes", "sha256", "artifact_id", "artifact_version_id", "artifact_ref",
        }
        if not isinstance(receipt, Mapping) or set(receipt) != fields:
            raise ValueError("native_artifact_receipt_invalid")
        name = receipt["output_name"]
        if not isinstance(name, str) or name not in expected_items or name in references:
            raise ValueError("native_artifact_receipt_output_mismatch")
        item = expected_items[name]
        provenance = {key: value for key, value in receipt.items()
                      if key not in {"artifact_id", "artifact_version_id", "artifact_ref"}}
        if (
            receipt["schema"] != "ananta.native-artifact-binding.v1"
            or receipt["classification"] != "technical_observation"
            or native_context_digest(receipt["binding"]) != native_context_digest(binding)
            or receipt["filename"] != item.filename or receipt["media_type"] != item.media_type
            or type(receipt["size_bytes"]) is not int or receipt["size_bytes"] != len(item.content)
            or receipt["sha256"] != item.sha256
            or receipt["tenant_id"] != tenant_id or receipt["project_id"] != project_id
        ):
            raise ValueError("native_artifact_receipt_binding_mismatch")
        digest = native_context_digest(provenance)
        artifact_id = f"native-artifact-{digest}"
        if (
            receipt["artifact_id"] != artifact_id or receipt["artifact_version_id"] != f"native-version-{digest}"
            or receipt["artifact_ref"] != f"artifact://{artifact_id}"
        ):
            raise ValueError("native_artifact_receipt_identity_mismatch")
        references[name] = receipt["artifact_ref"]
    return references
