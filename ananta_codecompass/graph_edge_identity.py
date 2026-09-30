"""Deterministic identity and value validation for CodeCompass graph edges.

Edge identifiers are content-addressed so that a rebuilt graph yields the
same identifiers for the same output records. The compact storage codec
re-derives them on load, therefore both sides share this single module.
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def finite_non_negative(
    value: Any,
    *,
    field: str,
    maximum: float | None = None,
) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"invalid_graph_edge_value:{field}")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0 or (maximum is not None and numeric > maximum):
        raise ValueError(f"invalid_graph_edge_value:{field}")
    return value


def stable_edge_id(
    record: dict[str, Any],
    *,
    source_id: str,
    target_id: str,
    raw_edge_type: str,
    occurrences: dict[str, int],
) -> str:
    explicit_edge_id = str(record.get("edge_id") or "").strip()
    identity_payload = {
        "source_id": source_id,
        "target_id": target_id,
        "raw_edge_type": raw_edge_type,
        "confidence": record.get("confidence"),
        "multiplicity": record.get("multiplicity"),
        "dependency_weight": record.get("dependency_weight"),
        "directed": record.get("directed"),
        "metrics": record.get("metrics"),
        "attributes": record.get("attributes"),
        "field": record.get("field"),
        "operation": record.get("operation"),
        "heuristic": record.get("heuristic"),
        "rule_id": record.get("rule_id"),
    }
    identity_seed = explicit_edge_id or (
        "edge:sha256:"
        + hashlib.sha256(
            json.dumps(
                identity_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
    )
    occurrence = occurrences.get(identity_seed, 0)
    if explicit_edge_id and occurrence:
        raise ValueError("duplicate_graph_edge_id")
    occurrences[identity_seed] = occurrence + 1
    if occurrence == 0:
        return identity_seed
    return "edge:sha256:" + hashlib.sha256(
        f"{identity_seed}\0{occurrence}".encode("utf-8")
    ).hexdigest()


def derived_edge_id(
    edge: dict[str, Any],
    *,
    strategy: str = "normalized",
) -> str:
    source_id = str(edge.get("source_id") or "").strip()
    target_id = str(edge.get("target_id") or "").strip()
    raw_edge_type = str(
        edge.get("raw_edge_type") or edge.get("edge_type") or "related"
    ) or "related"
    if not source_id or not target_id:
        return ""
    candidate = dict(edge)
    candidate.pop("edge_id", None)
    if (
        strategy == "legacy_confidence_default_omitted"
        and candidate.get("confidence") == 1.0
    ):
        candidate.pop("confidence", None)
    return stable_edge_id(
        candidate,
        source_id=source_id,
        target_id=target_id,
        raw_edge_type=raw_edge_type,
        occurrences={},
    )


def validate_edge_metric_values(record: dict[str, Any]) -> None:
    """Reject non-finite or negative optional edge metrics of an output record."""

    for metric_name in ("multiplicity", "dependency_weight"):
        if record.get(metric_name) is not None:
            finite_non_negative(record[metric_name], field=metric_name)
    if record.get("directed") is not None and not isinstance(record["directed"], bool):
        raise ValueError("invalid_graph_edge_value:directed")
    if isinstance(record.get("metrics"), dict):
        for metric_name, metric_value in record["metrics"].items():
            finite_non_negative(metric_value, field=f"metrics.{metric_name}")
