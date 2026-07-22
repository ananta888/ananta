"""Production policy authorizer and executor for hub-owned SFU commands."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol

from agent.services.sfu_broadcast_command_repository_port import (
    SfuBroadcastCommandMutation,
    SfuBroadcastCommandPolicyDecision,
    SfuBroadcastCommandRepositoryPort,
)
from agent.services.sfu_broadcast_command_service import (
    SfuBroadcastCommand,
    SfuBroadcastCommandAuditEvent,
    SfuBroadcastCommandAuthorization,
    SfuBroadcastCommandExecution,
    SfuBroadcastCommandPrincipal,
)


class SfuBroadcastFeaturePolicyPort(Protocol):
    def effective(self, tenant_id: str, region: str = "*", room_cohort: str = "*"): ...


class SfuBroadcastRoomAuthorityPort(Protocol):
    def resolve(self, *, tenant_id: str, room_id: str, actor_id: str): ...


class SfuBroadcastCommandPolicyEvaluator:
    """Evaluates only current Hub policy and authoritative room scope."""

    production_component = True
    _ROLES = frozenset({"user", "operator", "admin"})
    _KILL_REASONS = frozenset(
        {"immediate_security_fence", "stop_admission", "graceful_drain"}
    )

    def __init__(self, *, feature_policy, room_authority) -> None:
        self._feature_policy = feature_policy
        self._room_authority = room_authority

    def evaluate(
        self,
        principal: SfuBroadcastCommandPrincipal,
        command: SfuBroadcastCommand,
    ) -> SfuBroadcastCommandPolicyDecision:
        if principal.role not in self._ROLES:
            return self._deny("sfu_broadcast_permission_revoked")
        if principal.role != "admin" and command.room_ref not in principal.room_scopes:
            return self._deny("sfu_broadcast_permission_revoked")
        try:
            projection = self._feature_policy.effective(principal.tenant_ref)
        except Exception:
            return self._deny("sfu_broadcast_parent_not_ready")
        version = self._integer(getattr(projection, "version", 0))
        available = bool(getattr(projection, "available", False))
        reasons = frozenset(getattr(projection, "reason_codes", ()) or ())
        if command.action != "stop":
            if reasons & self._KILL_REASONS:
                return self._deny("sfu_broadcast_kill_switch_active", version)
            if not available:
                return self._deny("sfu_broadcast_parent_not_ready", version)
            flags = getattr(projection, "flags", {}) or {}
            enabled = (
                bool(flags.get("semantic_media_broadcast"))
                if isinstance(flags, Mapping)
                else "semantic_media_broadcast" in flags
            )
            if not enabled:
                return self._deny("sfu_broadcast_feature_disabled", version)
        try:
            scope = self._room_authority.resolve(
                tenant_id=principal.tenant_ref,
                room_id=command.room_ref,
                actor_id=principal.subject,
            )
        except Exception:
            return self._deny("sfu_broadcast_parent_not_ready", version)
        if command.action != "stop" and scope is None:
            return self._deny("sfu_broadcast_permission_revoked", version)
        return SfuBroadcastCommandPolicyDecision(
            allowed=True,
            authorization_reason="sfu_broadcast_command_authorized",
            execution_reason="sfu_broadcast_command_noop",
            policy_version=version,
            admission_epoch=self._integer_or_none(
                getattr(scope, "admission_epoch", None)
            ),
            membership_epoch=self._integer_or_none(
                getattr(scope, "membership_epoch", None)
            ),
        )

    @staticmethod
    def _integer(value) -> int:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0

    @staticmethod
    def _integer_or_none(value) -> int | None:
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _deny(
        execution_reason: str, policy_version: int = 0
    ) -> SfuBroadcastCommandPolicyDecision:
        return SfuBroadcastCommandPolicyDecision(
            allowed=False,
            authorization_reason="sfu_command_permission_denied",
            execution_reason=execution_reason,
            policy_version=policy_version,
        )


class SfuBroadcastPolicyCommandAuthorizer:
    production_component = True

    def __init__(self, evaluator: SfuBroadcastCommandPolicyEvaluator) -> None:
        self._evaluator = evaluator

    def authorize(self, principal, command) -> SfuBroadcastCommandAuthorization:
        decision = self._evaluator.evaluate(principal, command)
        return SfuBroadcastCommandAuthorization(
            allowed=decision.allowed,
            reason_code=decision.authorization_reason,
        )


class SfuBroadcastPolicyCommandExecutor:
    """Rechecks policy at the mutation boundary and delegates one SQL unit."""

    production_component = True

    def __init__(
        self,
        *,
        evaluator: SfuBroadcastCommandPolicyEvaluator,
        repository: SfuBroadcastCommandRepositoryPort,
        diagnostic_secret: bytes,
        retention: timedelta = timedelta(days=30),
    ) -> None:
        if len(diagnostic_secret) < 32:
            raise ValueError("diagnostic_secret must contain at least 32 bytes")
        self._evaluator = evaluator
        self._repository = repository
        self._secret = bytes(diagnostic_secret)
        self._retention = retention

    def execute(
        self,
        principal: SfuBroadcastCommandPrincipal,
        command: SfuBroadcastCommand,
        audit_event: SfuBroadcastCommandAuditEvent,
    ) -> SfuBroadcastCommandExecution:
        decision = self._evaluator.evaluate(principal, command)
        now = datetime.now(timezone.utc)
        options = command.options
        mutation = SfuBroadcastCommandMutation(
            tenant_id=principal.tenant_ref,
            room_id=command.room_ref,
            tenant_diagnostic_ref=self._diagnostic("tenant", principal.tenant_ref),
            room_diagnostic_ref=audit_event.room_diagnostic_ref,
            actor_diagnostic_ref=audit_event.actor_diagnostic_ref,
            actor_role=principal.role,
            operation_id=audit_event.operation_id,
            request_digest=self._request_digest(command),
            action=command.action,
            reason=command.reason,
            expected_version=command.expected_version,
            policy=decision,
            data_saver=options.get("data_saver"),
            audio_only=options.get("audio_only"),
            quality_preference=options.get("quality_preference"),
            now=now,
            retain_until=now + self._retention,
        )
        result = self._repository.execute(mutation)
        return SfuBroadcastCommandExecution(
            accepted=result.accepted,
            effective_version=result.effective_version,
            state=result.state,
            reason_code=result.reason_code,
            audit_committed=result.audit_committed,
        )

    def _diagnostic(self, label: str, value: str) -> str:
        return hmac.new(
            self._secret, (label + "\0" + value).encode("utf-8"), hashlib.sha256
        ).hexdigest()[:24]

    @staticmethod
    def _request_digest(command: SfuBroadcastCommand) -> str:
        payload = json.dumps(
            {
                "schema": command.schema,
                "room_ref": command.room_ref,
                "action": command.action,
                "expected_version": command.expected_version,
                "confirmed": command.confirmed,
                "options": dict(sorted(command.options.items())),
                "reason": command.reason,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
