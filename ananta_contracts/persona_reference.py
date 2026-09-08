"""Closed immutable persona asset metadata; never admission or publication authority."""

import re


def validate_reference(value, *, kind, error):
    if not isinstance(value, dict) or set(value) != {
        "tenant_id",
        "project_id",
        "artifact_id",
        "revision",
        "sha256",
        "kind",
        "classification",
    }:
        raise ValueError(error)
    for name in ("tenant_id", "project_id", "artifact_id"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", value[name]):
            raise ValueError(error)
    if (
        kind not in ("image", "video", "voice")
        or type(value["revision"]) is not int
        or value["revision"] != 1
        or value["kind"] != kind
        or not isinstance(value["sha256"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])
        or value["classification"] not in ("production", "synthetic", "test_only")
    ):
        raise ValueError(error)
    return value
