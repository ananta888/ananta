"""Closed, persistent purpose metadata for specialist evaluation partitions."""

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


def evaluation_dataset_purpose(manifest: Mapping[str, Any]) -> dict[str, str]:
    fields = ("execution_purpose", "evaluation_partition", "specialist_manifest_digest", "holdout_digest")
    if not any(key in manifest for key in fields):
        return {}
    if (
        manifest.get("execution_purpose") != "specialist_evaluation"
        or not isinstance(manifest.get("evaluation_partition"), str)
        or manifest.get("evaluation_partition") not in {"validation", "holdout"}
        or any(
            not isinstance(manifest.get(key), str) or re.fullmatch(r"[0-9a-f]{64}", manifest[key]) is None
            for key in ("specialist_manifest_digest", "holdout_digest")
        )
    ):
        raise ValueError("specialist_evaluation_dataset_purpose_invalid")
    return {key: manifest[key] for key in fields}


def evaluation_dataset_digest(purpose: Mapping[str, str], train_sha256: str, validation_sha256: str) -> str:
    identity = {
        "schema": "ananta.specialist-evaluation-dataset.v1",
        "purpose": dict(purpose),
        "train_sha256": train_sha256,
        "validation_sha256": validation_sha256,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
