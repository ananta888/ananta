"""Versioned provider invocation DTOs shared across Hub and Worker containers."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Any

from ananta_contracts.provider_endpoint_policy import (
    normalize_provider_endpoint_identity,
)

PROVIDER_INVOCATION_CONTEXT_SCHEMA = "ananta.provider-invocation-context.v1"
PROVIDER_BUDGET_DECISION_SCHEMA = "ananta.provider-budget-decision.v1"
_PROVIDER_CALL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,159}$")


class ProviderInvocationBlocked(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = str(reason_code or "provider_invocation_blocked")
        super().__init__(self.reason_code)


@dataclass(frozen=True)
class ProviderInvocationContext:
    tenant_id: str
    run_id: str
    policy_version: str
    prompt_version: str
    workflow_id: str = ""
    correlation_id: str = ""
    external_egress_allowed: bool = False
    secret_refs: tuple[str, ...] = ()
    max_attempts: int = 1
    max_total_tokens: int = 0
    max_cost_micros: int = 0
    deadline_epoch_seconds: float | None = None
    max_completion_tokens_per_call: int = 0
    estimated_cost_micros_per_1000_tokens: int = 0
    cache_enabled: bool = False
    require_hub_provider_budget: bool = False
    require_hub_provider_attempt_budget: bool = False
    require_hub_retry_budget: bool = False
    combined_retry_maximum: int = 0
    retry_attempt: int = 0
    retry_id: str = ""
    step_id: str = ""
    plan_hash: str = ""
    authorization_envelope: dict[str, Any] = field(default_factory=dict)
    attempt_id: str = ""
    fencing_token: int = 0
    provider_profile_id: str = ""
    selected_provider_id: str = ""
    selected_model_id: str = ""
    provider_binding_id: str = ""
    provider_endpoint_identity: str = ""
    provider_transport_mode: str = ""
    provider_decision_reason: str = ""
    provider_call_id: str = ""
    schema: str = PROVIDER_INVOCATION_CONTEXT_SCHEMA

    @classmethod
    def legacy_compatible(cls) -> "ProviderInvocationContext":
        """Explicit compatibility policy for calls not yet bound to a workflow."""

        return cls(
            tenant_id="legacy-system",
            # An unbound call has no Hub run whose aggregate budget can be
            # shared safely. Give each top-level invocation its own budget
            # scope; retries derive from and retain this context.
            run_id=f"legacy-unbound:{uuid.uuid4().hex}",
            policy_version="legacy-provider-policy-v1",
            prompt_version="legacy-prompt-v1",
            external_egress_allowed=True,
            max_attempts=64,
        )

    @classmethod
    def from_value(
        cls,
        value: "ProviderInvocationContext | dict[str, Any] | None",
    ) -> "ProviderInvocationContext":
        if isinstance(value, cls):
            return value
        if value is None:
            return cls.legacy_compatible()
        if not isinstance(value, dict):
            raise ProviderInvocationBlocked("provider_context_invalid")
        raw = dict(value)
        return cls(
            schema=str(
                raw.get("schema") or PROVIDER_INVOCATION_CONTEXT_SCHEMA
            ).strip(),
            tenant_id=str(raw.get("tenant_id") or "").strip(),
            run_id=str(raw.get("run_id") or "").strip(),
            workflow_id=str(raw.get("workflow_id") or "").strip(),
            correlation_id=str(raw.get("correlation_id") or "").strip(),
            policy_version=str(raw.get("policy_version") or "").strip(),
            prompt_version=str(raw.get("prompt_version") or "").strip(),
            external_egress_allowed=bool(raw.get("external_egress_allowed", False)),
            secret_refs=tuple(str(item) for item in raw.get("secret_refs", ())),
            max_attempts=int(raw.get("max_attempts", 1)),
            max_total_tokens=int(raw.get("max_total_tokens", 0)),
            max_cost_micros=int(raw.get("max_cost_micros", 0)),
            deadline_epoch_seconds=(
                float(raw["deadline_epoch_seconds"])
                if raw.get("deadline_epoch_seconds") is not None
                else None
            ),
            max_completion_tokens_per_call=int(
                raw.get("max_completion_tokens_per_call", 0)
            ),
            estimated_cost_micros_per_1000_tokens=int(
                raw.get("estimated_cost_micros_per_1000_tokens", 0)
            ),
            cache_enabled=bool(raw.get("cache_enabled", False)),
            require_hub_provider_budget=bool(
                raw.get("require_hub_provider_budget", False)
            ),
            require_hub_provider_attempt_budget=bool(
                raw.get("require_hub_provider_attempt_budget", False)
            ),
            require_hub_retry_budget=bool(
                raw.get("require_hub_retry_budget", False)
            ),
            combined_retry_maximum=int(raw.get("combined_retry_maximum", 0)),
            retry_attempt=int(raw.get("retry_attempt", 0)),
            retry_id=str(raw.get("retry_id") or "").strip(),
            step_id=str(raw.get("step_id") or "").strip(),
            plan_hash=str(raw.get("plan_hash") or "").strip(),
            authorization_envelope=dict(raw.get("authorization_envelope") or {}),
            attempt_id=str(raw.get("attempt_id") or "").strip(),
            fencing_token=int(raw.get("fencing_token") or 0),
            provider_profile_id=str(
                raw.get("provider_profile_id") or ""
            ).strip(),
            selected_provider_id=str(raw.get("selected_provider_id") or "").strip(),
            selected_model_id=str(raw.get("selected_model_id") or "").strip(),
            provider_binding_id=str(raw.get("provider_binding_id") or "").strip(),
            provider_endpoint_identity=str(
                raw.get("provider_endpoint_identity") or ""
            ).strip(),
            provider_transport_mode=str(
                raw.get("provider_transport_mode") or ""
            ).strip(),
            provider_decision_reason=str(
                raw.get("provider_decision_reason") or ""
            ).strip(),
            provider_call_id=str(raw.get("provider_call_id") or "").strip(),
        )

    def assert_valid(self) -> None:
        if self.schema != PROVIDER_INVOCATION_CONTEXT_SCHEMA:
            raise ProviderInvocationBlocked("provider_context_schema_unsupported")
        if not self.tenant_id or not self.run_id:
            raise ProviderInvocationBlocked("provider_context_binding_missing")
        if not self.policy_version or not self.prompt_version:
            raise ProviderInvocationBlocked("provider_context_version_missing")
        if self.max_attempts < 1:
            raise ProviderInvocationBlocked("provider_retry_budget_invalid")
        if (
            min(
                self.max_total_tokens,
                self.max_cost_micros,
                self.max_completion_tokens_per_call,
                self.estimated_cost_micros_per_1000_tokens,
            )
            < 0
        ):
            raise ProviderInvocationBlocked("provider_budget_invalid")
        if self.retry_attempt < 0 or self.combined_retry_maximum < 0:
            raise ProviderInvocationBlocked("provider_combined_retry_budget_invalid")
        if self.require_hub_retry_budget and self.combined_retry_maximum < 1:
            raise ProviderInvocationBlocked("provider_combined_retry_budget_required")
        if self.retry_attempt > 0 and self.require_hub_retry_budget and not self.retry_id:
            raise ProviderInvocationBlocked("provider_retry_id_required")
        if bool(self.selected_provider_id) != bool(self.selected_model_id):
            raise ProviderInvocationBlocked("provider_selection_binding_incomplete")
        if self.provider_endpoint_identity:
            if not self.selected_provider_id:
                raise ProviderInvocationBlocked(
                    "provider_endpoint_binding_incomplete"
                )
            try:
                normalized_endpoint = normalize_provider_endpoint_identity(
                    provider_id=self.selected_provider_id,
                    endpoint_url=self.provider_endpoint_identity,
                )
            except ValueError as exc:
                raise ProviderInvocationBlocked(
                    "provider_endpoint_identity_invalid"
                ) from exc
            if normalized_endpoint != self.provider_endpoint_identity:
                raise ProviderInvocationBlocked(
                    "provider_endpoint_identity_not_canonical"
                )
        if self.provider_transport_mode not in {"", "legacy", "none", "hub_bound"}:
            raise ProviderInvocationBlocked("provider_transport_mode_invalid")
        if self.provider_call_id and not _PROVIDER_CALL_ID.fullmatch(
            self.provider_call_id
        ):
            raise ProviderInvocationBlocked("provider_call_id_invalid")
        if self.require_hub_provider_budget and (
            not self.selected_provider_id
            or not self.selected_model_id
            or not self.provider_binding_id
            or self.provider_transport_mode != "hub_bound"
            or not self.provider_decision_reason
        ):
            raise ProviderInvocationBlocked("provider_selection_binding_required")
        if (
            self.require_hub_provider_attempt_budget
            and (
                not self.require_hub_provider_budget
                or not self.provider_profile_id
            )
        ):
            raise ProviderInvocationBlocked(
                "provider_attempt_budget_binding_required"
            )
        if self.provider_transport_mode == "none" and (
            self.selected_provider_id
            or self.selected_model_id
            or self.require_hub_provider_budget
        ):
            raise ProviderInvocationBlocked("provider_transport_binding_mismatch")
        if (self.require_hub_retry_budget or self.require_hub_provider_budget) and (
            not self.workflow_id
            or not self.step_id
            or not self.plan_hash
            or not self.authorization_envelope
            or not self.attempt_id
            or self.fencing_token < 1
        ):
            raise ProviderInvocationBlocked("provider_hub_binding_required")

    def for_attempt(
        self,
        attempt: int,
        *,
        retry_id: str,
    ) -> "ProviderInvocationContext":
        return replace(
            self,
            retry_attempt=max(0, int(attempt)),
            retry_id=str(retry_id).strip(),
        )

    def for_provider_call(self, provider_call_id: str) -> "ProviderInvocationContext":
        value = replace(
            self,
            provider_call_id=str(provider_call_id or "").strip(),
        )
        value.assert_valid()
        return value


@dataclass(frozen=True)
class ProviderBudgetDecision:
    allowed: bool
    reason_code: str
    attempts: int
    reserved_tokens: int
    reserved_cost_micros: int
    schema: str = PROVIDER_BUDGET_DECISION_SCHEMA


__all__ = [
    "PROVIDER_BUDGET_DECISION_SCHEMA",
    "PROVIDER_INVOCATION_CONTEXT_SCHEMA",
    "ProviderBudgetDecision",
    "ProviderInvocationBlocked",
    "ProviderInvocationContext",
]
