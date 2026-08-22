"""Canonical digest helpers shared by Hub-side HRM services."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping


def canonical_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def run_payload_digest(run_request: Mapping[str, Any]) -> str:
    unsigned = deepcopy(dict(run_request))
    authority = dict(unsigned.get("authority") or {})
    authority.pop("payload_digest", None)
    unsigned["authority"] = authority
    return canonical_digest(unsigned)


def contract_schema_digest() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "schemas"
        / "hrm-experiments"
        / "contracts.v1.json"
    )
    return hashlib.sha256(path.read_bytes()).hexdigest()


__all__ = ["canonical_digest", "contract_schema_digest", "run_payload_digest"]
