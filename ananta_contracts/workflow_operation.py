"""Stable operation identifiers shared by every workflow runtime."""

from __future__ import annotations

import hashlib
import json


def operation_id_for(
    *,
    tenant_id: str,
    run_id: str,
    step_id: str,
    declared_operation: str,
) -> str:
    values = tuple(str(value).strip() for value in (tenant_id, run_id, step_id, declared_operation))
    if any(not value for value in values):
        raise ValueError("operation_binding_required")
    rendered = json.dumps(
        list(values),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return "op-" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:32]


__all__ = ["operation_id_for"]
