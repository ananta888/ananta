"""Durable, content-free reservation receipt; never an execution result or capability."""

import json
import os
from pathlib import Path


def write_reservation_receipt(path: Path, *, reservation, source, companion, frontend_digest, profile):
    """Persist only closed provenance before execution; fail rather than overwrite."""
    value = {
        "schema": "ananta.meet-test-reservation-receipt.v1",
        "state": "reserved",
        "identity": {
            "issuer": "hub-evidence-registry",
            "source_id": reservation.source_id,
            "run_id": reservation.run_id,
            "binding_digest": reservation.binding_digest,
            "scope": "test",
            "synthetic": True,
        },
        "source": {key: source[key] for key in ("revision", "digest")},
        "companion": {key: companion[key] for key in ("revision", "digest")},
        "frontend_digest": frontend_digest,
        "profile": {key: profile[key] for key in ("name", "reference", "timeout_seconds")},
        "execution_result_available": False,
        "production_release_eligible": False,
    }
    encoded = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    if len(encoded) > 8192:
        raise ValueError("meet_reservation_receipt_budget")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as target:
        target.write(encoded)
        target.flush()
        os.fsync(target.fileno())
    # Persist the new directory entry where directory fsync is supported. This
    # is not a promise against hardware/filesystem failure or proof of liveness.
    if hasattr(os, "O_DIRECTORY"):
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
