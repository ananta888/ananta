"""Stable image receipt and task context helpers, independent of orchestration."""

import hashlib
import json


def admission_digest(admission):
    return hashlib.sha256(admission.model_dump_json().encode()).hexdigest()


def source_ids(admission):
    return tuple(
        sorted(
            value for value in (admission.origin_binding, admission.license_binding, admission.consent_binding) if value
        )
    )


def image_receipt(image, expected_source):
    if (
        image.source_sha256 != expected_source
        or not isinstance(image.png, bytes)
        or not isinstance(image.preview, bytes)
        or not 0 < len(image.png) <= 5 * 1024 * 1024
        or not 0 < len(image.preview) <= 350_000
        or hashlib.sha256(image.png).hexdigest() != image.image_sha256
        or hashlib.sha256(image.preview).hexdigest() != image.preview_sha256
    ):
        raise ValueError("persona_inspection_result_invalid")
    return receipt_digest(
        source_sha256=expected_source,
        image_sha256=image.image_sha256,
        preview_sha256=image.preview_sha256,
        image_size=len(image.png),
        preview_size=len(image.preview),
    )


def receipt_digest(*, source_sha256, image_sha256, preview_sha256, image_size, preview_size):
    payload = {
        "schema": "ananta.persona-inspection-receipt.v1",
        "source_sha256": source_sha256,
        "image_sha256": image_sha256,
        "preview_sha256": preview_sha256,
        "image_size": image_size,
        "preview_size": preview_size,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def task_context(assignment):
    return {
        key: assignment[key]
        for key in (
            "lease_id",
            "assignment_id",
            "run_id",
            "run_binding_digest",
            "admission_digest",
            "owner_subject",
            "source_sha256",
            "deadline",
        )
    }
