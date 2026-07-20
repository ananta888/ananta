"""Closed, transport-neutral contracts for semantic visual execution.

These validators never grant capture permission, contracts, leases, or quality.
They only validate bounded executor evidence.  Hub services remain the sole
authority for admission and scheduling.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Mapping

SCENE_SCHEMA = "ananta.semantic-scene.v1"
FRAME_SCHEMA = "ananta.semantic-frame.v1"
RESIDUAL_SCHEMA = "ananta.visual-residual-chunk.v1"
REPORT_SCHEMA = "ananta.reconstruction-report.v1"
VALIDATOR_REPORT_SCHEMA = "ananta.semantic-validator-report.v1"

MAX_SCENE_BYTES = 256 * 1024
MAX_FRAME_BYTES = 256 * 1024
MAX_REPORT_BYTES = 32 * 1024
MAX_NODES = 256
MAX_DEPTH = 8
MAX_POINTS = 32

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,191}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ALGORITHM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,255}$")


class SemanticVisualContractError(ValueError):
    """A stable, machine-readable visual contract rejection."""

    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


def canonical_json(value: Mapping[str, Any], *, max_bytes: int) -> bytes:
    _finite(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SemanticVisualContractError("invalid_json", "value must be finite JSON") from exc
    if len(encoded) > max_bytes:
        raise SemanticVisualContractError("payload_too_large", "payload exceeds byte budget")
    return encoded


def visual_digest(value: Mapping[str, Any], *, max_bytes: int = MAX_SCENE_BYTES) -> str:
    return hashlib.sha256(canonical_json(value, max_bytes=max_bytes)).hexdigest()


def validate_semantic_scene(
    raw: object,
    *,
    now_ms: int | None = None,
    maximum_age_ms: int = 60_000,
) -> dict[str, Any]:
    payload = _exact(
        raw,
        required={
            "schema", "scene_id", "session_id", "contract_id", "contract_digest",
            "epoch", "sequence", "source_frame_digest", "coordinate_space",
            "timebase", "provenance", "nodes", "security",
        },
        reason="invalid_scene",
    )
    canonical_json(payload, max_bytes=MAX_SCENE_BYTES)
    if payload["schema"] != SCENE_SCHEMA:
        _raise("invalid_schema", "unknown scene schema")
    for field in ("scene_id", "session_id", "contract_id"):
        _identifier(payload[field], field)
    for field in ("contract_digest", "source_frame_digest"):
        _digest(payload[field], field)
    _integer(payload["epoch"], "epoch", 1, 2_147_483_647)
    _integer(payload["sequence"], "sequence", 0, 9_007_199_254_740_991)
    coordinates = _exact(
        payload["coordinate_space"], required={"unit", "origin", "width", "height"}, reason="invalid_coordinates"
    )
    if coordinates != {"unit": "normalized", "origin": "top_left", "width": 1, "height": 1}:
        _raise("invalid_coordinates", "scene uses an unsupported coordinate space")
    timebase = _exact(
        payload["timebase"], required={"unit", "captured_at_ms", "duration_ms"}, reason="invalid_timebase"
    )
    if timebase["unit"] != "milliseconds":
        _raise("invalid_timebase", "time unit must be milliseconds")
    captured_at_ms = _integer(timebase["captured_at_ms"], "captured_at_ms", 0, 9_007_199_254_740_991)
    if now_ms is not None and not now_ms - maximum_age_ms <= captured_at_ms <= now_ms:
        _raise("stale_scene", "scene capture time is outside the admitted live window")
    _integer(timebase["duration_ms"], "duration_ms", 0, 60_000)
    _provenance(payload["provenance"])
    security = _exact(
        payload["security"], required={"classification", "raw_media_included"}, reason="unknown_security_field"
    )
    if security != {"classification": "derived_semantic_metadata", "raw_media_included": False}:
        _raise("invalid_security", "scene may contain derived metadata only")
    nodes = payload["nodes"]
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        _raise("node_limit_exceeded", "nodes must be a bounded array")
    parents: dict[str, str | None] = {}
    for node in nodes:
        validated = _node(node)
        node_id = validated["id"]
        if node_id in parents:
            _raise("duplicate_node", "node ids must be unique")
        parents[node_id] = validated["parent_id"]
    for node_id, parent_id in parents.items():
        if parent_id is not None and parent_id not in parents:
            _raise("missing_parent", f"node {node_id} references a missing parent")
        seen: set[str] = set()
        cursor: str | None = node_id
        depth = 0
        while cursor is not None:
            if cursor in seen:
                _raise("scene_cycle", "scene hierarchy contains a cycle")
            seen.add(cursor)
            depth += 1
            if depth > MAX_DEPTH:
                _raise("scene_depth_exceeded", "scene hierarchy exceeds maximum depth")
            cursor = parents.get(cursor)
    return json.loads(json.dumps(payload, sort_keys=True, allow_nan=False))


def _node(raw: object) -> Mapping[str, Any]:
    node = _exact(
        raw,
        required={"id", "parent_id", "kind", "geometry", "motion", "confidence"},
        optional={"label"},
        reason="invalid_node",
    )
    _identifier(node["id"], "node.id")
    if node["parent_id"] is not None:
        _identifier(node["parent_id"], "node.parent_id")
    kind = _observation(node["kind"])
    if kind["value"] not in {"region", "text_hint", "object_hint", "cursor", "unknown"}:
        _raise("invalid_node_kind", "unknown node kind")
    geometry = _observation(node["geometry"])["value"]
    geometry = _exact(
        geometry, required={"x", "y", "width", "height", "outline"}, reason="invalid_geometry"
    )
    x = _number(geometry["x"], "x", 0, 1)
    y = _number(geometry["y"], "y", 0, 1)
    width = _number(geometry["width"], "width", 0, 1, exclusive_min=True)
    height = _number(geometry["height"], "height", 0, 1, exclusive_min=True)
    if x + width > 1 or y + height > 1:
        _raise("geometry_out_of_bounds", "region extends beyond normalized bounds")
    outline = geometry["outline"]
    if not isinstance(outline, list) or len(outline) > MAX_POINTS:
        _raise("point_limit_exceeded", "outline exceeds point budget")
    for point in outline:
        point = _exact(point, required={"x", "y"}, reason="invalid_point")
        _number(point["x"], "point.x", 0, 1)
        _number(point["y"], "point.y", 0, 1)
    motion = _observation(node["motion"])["value"]
    motion = _exact(motion, required={"dx_per_second", "dy_per_second"}, reason="invalid_motion")
    _number(motion["dx_per_second"], "dx_per_second", -4, 4)
    _number(motion["dy_per_second"], "dy_per_second", -4, 4)
    confidence = _observation(node["confidence"])["value"]
    _number(confidence, "confidence", 0, 1)
    if "label" in node:
        label = _observation(node["label"])["value"]
        if not isinstance(label, str) or len(label) > 256:
            _raise("invalid_label", "label exceeds its string budget")
    return node


def _observation(raw: object) -> Mapping[str, Any]:
    observation = _exact(raw, required={"value", "provenance"}, reason="invalid_observation")
    _provenance(observation["provenance"])
    return observation


def _provenance(raw: object) -> None:
    provenance = _exact(
        raw, required={"source", "algorithm", "version", "authoritative"}, reason="invalid_provenance"
    )
    if provenance["source"] not in {"heuristic", "user", "model_proposal"}:
        _raise("invalid_provenance", "unknown provenance source")
    for field in ("algorithm", "version"):
        if not isinstance(provenance[field], str) or not _ALGORITHM.fullmatch(provenance[field]):
            _raise("invalid_provenance", f"invalid provenance {field}")
    if not isinstance(provenance["authoritative"], bool):
        _raise("invalid_provenance", "authoritative must be boolean")
    if provenance["source"] == "model_proposal" and provenance["authoritative"]:
        _raise("model_not_authoritative", "model proposals cannot be authoritative")


def _exact(
    raw: object,
    *,
    required: set[str],
    optional: set[str] | None = None,
    reason: str,
) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        _raise(reason, "value must be an object")
    keys = set(raw)
    allowed = required | (optional or set())
    if keys - allowed:
        _raise(reason, f"unknown fields: {sorted(keys - allowed)}")
    if required - keys:
        _raise(reason, f"missing fields: {sorted(required - keys)}")
    return raw


def _identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _raise("invalid_identifier", f"invalid {field}")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        _raise("invalid_digest", f"invalid {field}")
    return value


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _raise("invalid_integer", f"invalid {field}")
    return value


def _number(value: object, field: str, minimum: float, maximum: float, *, exclusive_min: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _raise("invalid_number", f"invalid {field}")
    numeric = float(value)
    if (numeric <= minimum if exclusive_min else numeric < minimum) or numeric > maximum:
        _raise("invalid_number", f"invalid {field}")
    return numeric


def _finite(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        _raise("non_finite", "non-finite numbers are forbidden")
    if isinstance(value, Mapping):
        for item in value.values():
            _finite(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _finite(item)


def _raise(reason: str, message: str) -> None:
    raise SemanticVisualContractError(reason, message)
