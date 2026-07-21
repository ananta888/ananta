"""Fail-closed feature policy for the semantic media and speech programme.

The Hub owns the effective state.  Browser preferences are never treated as
authority; the network-profile endpoint projects this policy to clients.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

from agent.services.sfu_broadcast_feature_policy import (
    SFB_BROADCAST_FEATURE_DEFINITIONS,
)

@dataclass(frozen=True, slots=True)
class SemanticMediaFeatureDefinition:
    key: str
    env_key: str
    owner: str
    scope: str
    default: bool = False
    depends_on: tuple[str, ...] = ()
    background_operation: bool = False


SEMANTIC_MEDIA_BACKGROUND_OPERATIONS = "semantic_media_background_operations"

SEMANTIC_MEDIA_FEATURE_CATALOG: tuple[SemanticMediaFeatureDefinition, ...] = (
    SemanticMediaFeatureDefinition(
        key="ordinary_media_publication",
        env_key="ANANTA_ORDINARY_MEDIA_PUBLICATION_ENABLED",
        owner="hub",
        scope="pair_session",
    ),
    SemanticMediaFeatureDefinition(
        key="semantic_visual_capture",
        env_key="ANANTA_SEMANTIC_VISUAL_CAPTURE_ENABLED",
        owner="hub",
        scope="pair_publication",
    ),
    SemanticMediaFeatureDefinition(
        key="semantic_speech_runtime",
        env_key="ANANTA_SEMANTIC_SPEECH_RUNTIME_ENABLED",
        owner="hub",
        scope="voice_run",
    ),
    SemanticMediaFeatureDefinition(
        key="semantic_media_sfu",
        env_key="ANANTA_SEMANTIC_MEDIA_SFU_ENABLED",
        owner="hub",
        scope="pair_session",
    ),
    SemanticMediaFeatureDefinition(
        key=SEMANTIC_MEDIA_BACKGROUND_OPERATIONS,
        env_key="ANANTA_SEMANTIC_MEDIA_BACKGROUND_OPERATIONS_ENABLED",
        owner="hub",
        scope="deployment",
    ),
    SemanticMediaFeatureDefinition(
        key="peer_evidence_sync",
        env_key="ANANTA_PEER_EVIDENCE_SYNC_ENABLED",
        owner="hub",
        scope="pair_direction",
        depends_on=("semantic_speech_runtime", SEMANTIC_MEDIA_BACKGROUND_OPERATIONS),
        background_operation=True,
    ),
    SemanticMediaFeatureDefinition(
        key="speech_reconciliation",
        env_key="ANANTA_SPEECH_RECONCILIATION_ENABLED",
        owner="hub",
        scope="dataset_scope",
        depends_on=("peer_evidence_sync", SEMANTIC_MEDIA_BACKGROUND_OPERATIONS),
        background_operation=True,
    ),
    SemanticMediaFeatureDefinition(
        key="speech_adaptation_training",
        env_key="ANANTA_SPEECH_ADAPTATION_TRAINING_ENABLED",
        owner="hub",
        scope="training_profile",
        depends_on=("speech_reconciliation", SEMANTIC_MEDIA_BACKGROUND_OPERATIONS),
        background_operation=True,
    ),
    SemanticMediaFeatureDefinition(
        key="speech_adapter_routing",
        env_key="ANANTA_SPEECH_ADAPTER_ROUTING_ENABLED",
        owner="hub",
        scope="inference_profile",
        depends_on=("speech_adaptation_training", SEMANTIC_MEDIA_BACKGROUND_OPERATIONS),
        background_operation=True,
    ),
    *(
        SemanticMediaFeatureDefinition(
            key=definition.key,
            env_key=definition.env_key,
            owner=definition.owner,
            scope=definition.scope,
            default=definition.default,
            depends_on=definition.depends_on,
            background_operation=definition.background_operation,
        )
        for definition in SFB_BROADCAST_FEATURE_DEFINITIONS
    ),
)

SEMANTIC_MEDIA_FLAG_DEFAULTS: dict[str, bool] = {
    definition.key: definition.default for definition in SEMANTIC_MEDIA_FEATURE_CATALOG
}


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


def resolve_semantic_media_feature_flags(source: Mapping[str, Any] | None = None) -> dict[str, bool]:
    """Return Hub-effective flags with dependency and kill-switch fencing.

    ``source`` may contain canonical setting keys or environment keys.  A
    canonical key takes precedence.  Missing, unknown and malformed values do
    not activate capabilities.
    """

    raw = source or {}
    resolved: dict[str, bool] = {}
    for definition in SEMANTIC_MEDIA_FEATURE_CATALOG:
        candidate = raw.get(definition.key, raw.get(definition.env_key, definition.default))
        requested = _strict_boolean(candidate)
        resolved[definition.key] = requested and all(resolved.get(dep, False) for dep in definition.depends_on)
    return resolved


def semantic_media_feature_catalog_payload() -> list[dict[str, Any]]:
    """Return a JSON-safe catalog without exposing configuration values."""

    return [asdict(definition) for definition in SEMANTIC_MEDIA_FEATURE_CATALOG]


def background_operations_enabled(flags: Mapping[str, Any] | None) -> bool:
    return resolve_semantic_media_feature_flags(flags).get(SEMANTIC_MEDIA_BACKGROUND_OPERATIONS, False)


__all__ = [
    "SEMANTIC_MEDIA_BACKGROUND_OPERATIONS",
    "SEMANTIC_MEDIA_FEATURE_CATALOG",
    "SEMANTIC_MEDIA_FLAG_DEFAULTS",
    "SemanticMediaFeatureDefinition",
    "background_operations_enabled",
    "resolve_semantic_media_feature_flags",
    "semantic_media_feature_catalog_payload",
]
