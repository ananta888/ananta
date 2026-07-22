"""Hub-owned static and persistent policy for SFU broadcast rollout."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.repositories.sfu_broadcast_feature_flag_repository import (
    SfuBroadcastFeatureFlagMutation,
    SfuBroadcastFeatureFlagMutationResult,
    SfuBroadcastFeatureFlagRepositoryError,
    SfuBroadcastFeatureFlagRepositoryPort,
    SfuBroadcastFeatureFlagScope,
    SfuBroadcastFeatureFlagState,
)


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

SFB_BROADCAST_FEATURE_KEYS = frozenset(
    definition.key for definition in SFB_BROADCAST_FEATURE_DEFINITIONS
)
SFB_BROADCAST_KILL_SWITCH_REASON_CODES: Mapping[str, str] = {
    "immediate_security_fence": "sfu_broadcast.kill_switch.immediate_security_fence",
    "stop_admission": "sfu_broadcast.kill_switch.stop_admission",
    "graceful_drain": "sfu_broadcast.kill_switch.graceful_drain",
}
SFB_BROADCAST_KILL_SWITCH_PRIORITY = (
    "immediate_security_fence",
    "stop_admission",
    "graceful_drain",
)
SFB_BROADCAST_MUTABLE_FLAGS = SFB_BROADCAST_FEATURE_KEYS | frozenset(
    SFB_BROADCAST_KILL_SWITCH_REASON_CODES
)
SFB_BROADCAST_ROOM_COHORTS = frozenset(
    {"*", "internal", "canary", "general_availability"}
)
SFB_BROADCAST_ROLLOUT_STAGES = frozenset(
    {"flag_off", "internal", "cohort", "canary", "general_availability", "security"}
)


class SfuBroadcastFeaturePolicyError(ValueError):
    def __init__(self, reason_code: str, *, status_code: int = 400) -> None:
        self.reason_code = reason_code
        self.status_code = status_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureMutationCommand:
    tenant_id: str
    region: str
    room_cohort: str
    flag: str
    enabled: bool
    rollout_stage: str
    expected_version: int
    actor: str
    reason: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureMutationOutcome:
    status: str
    state: SfuBroadcastFeatureFlagState
    reason_code: str

    def payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "flag": self.state.flag,
            "enabled": bool(self.state.enabled),
            "rollout_stage": self.state.rollout_stage,
            "version": self.state.version,
            "tenant_id": self.state.scope.tenant_id,
            "region": self.state.scope.region,
            "room_cohort": self.state.scope.room_cohort,
            "actor": self.state.actor,
            "reason": self.state.reason,
            "audited_at": self.state.audited_at,
        }


@dataclass(frozen=True, slots=True)
class SfuBroadcastFeatureProjection:
    version: int
    flags: Mapping[str, bool]
    available: bool
    reason_codes: tuple[str, ...] = ()

    @classmethod
    def unavailable(cls, reason_code: str) -> "SfuBroadcastFeatureProjection":
        return cls(
            version=0,
            flags={key: False for key in SFB_BROADCAST_FEATURE_KEYS},
            available=False,
            reason_codes=(reason_code,),
        )

    def payload(self) -> dict[str, object]:
        return {
            "version": max(0, int(self.version)),
            "available": self.available is True,
            "flags": {
                key: self.flags.get(key) is True
                for key in sorted(SFB_BROADCAST_FEATURE_KEYS)
            },
            "reason_codes": list(self.reason_codes),
        }


class SfuBroadcastFeaturePolicy:
    """Small Hub use case over the persistent repository port."""

    def __init__(
        self,
        repository: SfuBroadcastFeatureFlagRepositoryPort,
        *,
        static_source: Mapping[str, Any] | None = None,
        known_room_cohorts: frozenset[str] = SFB_BROADCAST_ROOM_COHORTS,
    ) -> None:
        self._repository = repository
        self._static_source = dict(static_source or {})
        self._known_room_cohorts = frozenset(known_room_cohorts)

    def mutate(
        self,
        command: SfuBroadcastFeatureMutationCommand,
    ) -> SfuBroadcastFeatureMutationOutcome:
        self._validate_command(command)
        mutation = SfuBroadcastFeatureFlagMutation(
            scope=SfuBroadcastFeatureFlagScope(
                tenant_id=command.tenant_id,
                region=command.region,
                room_cohort=command.room_cohort,
            ),
            flag=command.flag,
            enabled=command.enabled,
            rollout_stage=command.rollout_stage,
            actor=command.actor,
            reason=command.reason,
            idempotency_key=command.idempotency_key,
        )
        try:
            result = (
                self._repository.create(mutation, expected_version=0)
                if command.expected_version == 0
                else self._repository.compare_and_swap(
                    mutation,
                    expected_version=command.expected_version,
                )
            )
        except SfuBroadcastFeatureFlagRepositoryError as exc:
            raise SfuBroadcastFeaturePolicyError(
                exc.reason_code,
                status_code=409 if "conflict" in exc.reason_code else 400,
            ) from exc
        self._require_committed_audit(result)
        assert result.state is not None
        return SfuBroadcastFeatureMutationOutcome(
            status=result.status,
            state=result.state,
            reason_code=SFB_BROADCAST_KILL_SWITCH_REASON_CODES.get(
                command.flag,
                "sfu_broadcast.feature_flag_changed",
            ),
        )

    def effective(
        self,
        *,
        tenant_id: str,
        region: str = "*",
        room_cohort: str = "*",
    ) -> SfuBroadcastFeatureProjection:
        _bounded_identifier(tenant_id, "feature_flag_tenant_invalid", maximum=255)
        _bounded_identifier(region, "feature_flag_region_invalid", maximum=128, wildcard=True)
        if room_cohort not in self._known_room_cohorts:
            raise SfuBroadcastFeaturePolicyError("feature_flag_room_cohort_unknown")

        states: dict[str, SfuBroadcastFeatureFlagState] = {}
        for scope in _ordered_scopes(tenant_id, region, room_cohort):
            try:
                snapshot = self._repository.snapshot(scope)
            except (SfuBroadcastFeatureFlagRepositoryError, RuntimeError):
                return SfuBroadcastFeatureProjection.unavailable(
                    "sfu_broadcast.feature_store_unavailable"
                )
            if not snapshot.available:
                return SfuBroadcastFeatureProjection.unavailable(
                    "sfu_broadcast.feature_store_unavailable"
                )
            for flag, state in snapshot.flags.items():
                if flag in SFB_BROADCAST_MUTABLE_FLAGS:
                    states[flag] = state

        requested = _requested_static_flags(self._static_source)
        for key in SFB_BROADCAST_FEATURE_KEYS:
            state = states.get(key)
            if state is not None:
                requested[key] = state.enabled is True
        resolved = resolve_sfu_broadcast_feature_flags(requested)
        active_kill_switches = tuple(
            flag
            for flag in SFB_BROADCAST_KILL_SWITCH_PRIORITY
            if (states.get(flag) is not None and states[flag].enabled is True)
        )
        if active_kill_switches:
            resolved = {key: False for key in SFB_BROADCAST_FEATURE_KEYS}
        return SfuBroadcastFeatureProjection(
            version=max((state.version for state in states.values()), default=0),
            flags=resolved,
            available=True,
            reason_codes=tuple(
                SFB_BROADCAST_KILL_SWITCH_REASON_CODES[flag]
                for flag in active_kill_switches
            ),
        )

    def _validate_command(self, command: SfuBroadcastFeatureMutationCommand) -> None:
        _bounded_identifier(command.tenant_id, "feature_flag_tenant_invalid", maximum=255)
        _bounded_identifier(command.region, "feature_flag_region_invalid", maximum=128, wildcard=True)
        if command.room_cohort not in self._known_room_cohorts:
            raise SfuBroadcastFeaturePolicyError("feature_flag_room_cohort_unknown")
        if command.flag not in SFB_BROADCAST_MUTABLE_FLAGS:
            raise SfuBroadcastFeaturePolicyError("feature_flag_unknown")
        if type(command.enabled) is not bool:
            raise SfuBroadcastFeaturePolicyError("feature_flag_value_invalid")
        if command.rollout_stage not in SFB_BROADCAST_ROLLOUT_STAGES:
            raise SfuBroadcastFeaturePolicyError("feature_flag_rollout_stage_unknown")
        if isinstance(command.expected_version, bool) or not isinstance(command.expected_version, int):
            raise SfuBroadcastFeaturePolicyError("feature_flag_expected_version_invalid")
        if command.expected_version < 0:
            raise SfuBroadcastFeaturePolicyError("feature_flag_expected_version_invalid")
        _bounded_text(command.actor, "feature_flag_actor_invalid", maximum=255)
        _bounded_text(command.reason, "feature_flag_reason_invalid", maximum=1_024)
        _bounded_text(
            command.idempotency_key,
            "feature_flag_idempotency_key_invalid",
            minimum=8,
            maximum=255,
            reject_whitespace=True,
        )

    @staticmethod
    def _require_committed_audit(result: SfuBroadcastFeatureFlagMutationResult) -> None:
        if result.status == "unavailable":
            raise SfuBroadcastFeaturePolicyError(
                result.reason_code or "feature_flag_store_unavailable",
                status_code=503,
            )
        if result.status == "conflict":
            raise SfuBroadcastFeaturePolicyError(
                result.reason_code or "feature_flag_version_conflict",
                status_code=409,
            )
        state = result.state
        if not result.committed or state is None:
            raise SfuBroadcastFeaturePolicyError(
                "feature_flag_mutation_rejected",
                status_code=503,
            )
        if (
            state.audited_at <= 0
            or len(state.idempotency_key_digest) != 64
            or any(character not in "0123456789abcdef" for character in state.idempotency_key_digest)
        ):
            raise SfuBroadcastFeaturePolicyError(
                "feature_flag_audit_missing",
                status_code=503,
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


def _requested_static_flags(source: Mapping[str, Any]) -> dict[str, bool]:
    return {
        definition.key: _strict_boolean(
            source.get(definition.key, source.get(definition.env_key, definition.default))
        )
        for definition in SFB_BROADCAST_FEATURE_DEFINITIONS
    }


def _ordered_scopes(
    tenant_id: str,
    region: str,
    room_cohort: str,
) -> tuple[SfuBroadcastFeatureFlagScope, ...]:
    candidates = (
        SfuBroadcastFeatureFlagScope(tenant_id, "*", "*"),
        SfuBroadcastFeatureFlagScope(tenant_id, region, "*"),
        SfuBroadcastFeatureFlagScope(tenant_id, "*", room_cohort),
        SfuBroadcastFeatureFlagScope(tenant_id, region, room_cohort),
    )
    unique: list[SfuBroadcastFeatureFlagScope] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _bounded_identifier(
    value: str,
    reason_code: str,
    *,
    maximum: int,
    wildcard: bool = False,
) -> None:
    if wildcard and value == "*":
        return
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(not (character.isalnum() or character in "._:-") for character in value)
    ):
        raise SfuBroadcastFeaturePolicyError(reason_code)


def _bounded_text(
    value: str,
    reason_code: str,
    *,
    minimum: int = 1,
    maximum: int,
    reject_whitespace: bool = False,
) -> None:
    if (
        not isinstance(value, str)
        or not minimum <= len(value.strip()) <= maximum
        or any(ord(character) < 32 for character in value)
        or (reject_whitespace and any(character.isspace() for character in value))
    ):
        raise SfuBroadcastFeaturePolicyError(reason_code)


__all__ = [
    "SFB_BROADCAST_FEATURE_KEYS",
    "SfuBroadcastFeatureDefinition",
    "SfuBroadcastFeatureMutationCommand",
    "SfuBroadcastFeatureMutationOutcome",
    "SfuBroadcastFeaturePolicy",
    "SfuBroadcastFeaturePolicyError",
    "SfuBroadcastFeatureProjection",
    "SFB_BROADCAST_FEATURE_DEFINITIONS",
    "SFB_BROADCAST_KILL_SWITCH_REASON_CODES",
    "SFB_BROADCAST_MUTABLE_FLAGS",
    "SFB_BROADCAST_ROLLOUT_STAGES",
    "SFB_BROADCAST_ROOM_COHORTS",
    "resolve_sfu_broadcast_feature_flags",
]
