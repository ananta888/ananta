"""Static feature policy primitives for SFU broadcast defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureDefinition:
    key: str
    env_key: str
    owner: str
    scope: str
    default: bool = False
    depends_on: tuple[str, ...] = ()
    background_operation: bool = False


SFB_BROADCAST_FEATURE_DEFINITIONS: tuple[SfuBroadcastFeatureDefinition, ...] = (
    SfuBroadcastFeatureDefinition(
        key="semantic_media_broadcast",
        env_key="ANANTA_SEMANTIC_MEDIA_BROADCAST_ENABLED",
        owner="hub",
        scope="pair_session",
    ),
    SfuBroadcastFeatureDefinition(
        key="semantic_media_receiver_groups",
        env_key="ANANTA_SEMANTIC_MEDIA_RECEIVER_GROUPS_ENABLED",
        owner="hub",
        scope="pair_session",
        depends_on=("semantic_media_broadcast",),
    ),
    SfuBroadcastFeatureDefinition(
        key="semantic_media_fleet_admission",
        env_key="ANANTA_SEMANTIC_MEDIA_FLEET_ADMISSION_ENABLED",
        owner="hub",
        scope="pair_session",
        depends_on=("semantic_media_broadcast",),
    ),
    SfuBroadcastFeatureDefinition(
        key="semantic_media_turn_cost_controls",
        env_key="ANANTA_SEMANTIC_MEDIA_TURN_COST_CONTROLS_ENABLED",
        owner="hub",
        scope="pair_session",
        depends_on=("semantic_media_broadcast",),
    ),
)


def _strict_boolean(value: Any) -> bool:
    """Accept booleans and explicit env literals; everything else is false."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
    return False


def resolve_sfu_broadcast_feature_flags(source: Mapping[str, Any] | None = None) -> dict[str, bool]:
    """Resolve broadcast feature dependencies and return canonical booleans."""

    raw = source or {}
    resolved: dict[str, bool] = {}
    for definition in SFB_BROADCAST_FEATURE_DEFINITIONS:
        candidate = raw.get(definition.key, raw.get(definition.env_key, definition.default))
        requested = _strict_boolean(candidate)
        resolved[definition.key] = requested and all(resolved.get(dep, False) for dep in definition.depends_on)
    return resolved


__all__ = [
    "SfuBroadcastFeatureDefinition",
    "SFB_BROADCAST_FEATURE_DEFINITIONS",
    "resolve_sfu_broadcast_feature_flags",
]
