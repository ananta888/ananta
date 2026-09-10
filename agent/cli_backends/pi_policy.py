"""Closed Pi invocation projection; budget ownership stays with the Hub port."""

from __future__ import annotations

import copy
import math
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from agent.cli_backends.pi_configuration import PI_SYSTEM_PROMPT
from ananta_contracts.coding_agent_target import CodingAgentInferenceTarget
from ananta_contracts.provider_endpoint_policy import (
    is_forbidden_provider_endpoint_target,
    is_local_provider_endpoint,
    normalize_provider_endpoint_identity,
    validate_provider_endpoint_resolution,
)
from ananta_contracts.provider_invocation import (
    PROVIDER_BUDGET_DECISION_SCHEMA,
    ProviderBudgetDecision,
    ProviderInvocationBlocked,
    ProviderInvocationContext,
)

if TYPE_CHECKING:
    from agent.services.provider_invocation_middleware import ProviderBudgetPort
    from ananta_contracts.provider_endpoint_policy import ProviderEndpointResolver


@dataclass(frozen=True)
class PiExecutionProjection:
    target: CodingAgentInferenceTarget
    max_tokens: int
    deadline: float


class PiInvocationPolicy:
    """One delegated call, not an authority issuer or a local budget ledger.

    The caller must additionally revalidate task/lease authority with the Hub.
    A context DTO (even one that passes these checks) is not proof of authority.
    """

    def __init__(
        self,
        *,
        context: ProviderInvocationContext,
        budget: ProviderBudgetPort,
        clock: Callable[[], float] = time.time,
        resolver: ProviderEndpointResolver | None = None,
    ) -> None:
        if not isinstance(context, ProviderInvocationContext):
            raise ProviderInvocationBlocked("pi_hub_context_required")
        self._context = copy.deepcopy(context)
        self._budget, self._clock, self._resolver = budget, clock, resolver
        self._lock, self._consumed = threading.Lock(), False

    def project(self, target: CodingAgentInferenceTarget) -> PiExecutionProjection:
        context = self._context
        try:
            context.assert_valid()
        except (TypeError, ValueError) as exc:
            raise ProviderInvocationBlocked("pi_hub_context_invalid") from exc
        if (
            context.require_hub_provider_budget is not True
            or self._budget is None
            or not context.provider_call_id
            or not context.provider_endpoint_identity
            or context.provider_transport_mode != "hub_bound"
        ):
            raise ProviderInvocationBlocked("pi_hub_context_required")
        # Profile attempts are reserved atomically by the existing Hub budget
        # port under the signed profile plan. No Worker retry loop is added.
        if context.require_hub_retry_budget or context.retry_attempt:
            raise ProviderInvocationBlocked("pi_retry_policy_unsupported")
        for value in (
            context.max_total_tokens,
            context.max_completion_tokens_per_call,
            context.max_attempts,
            context.fencing_token,
        ):
            if type(value) is not int or value < 1:
                raise ProviderInvocationBlocked("pi_budget_invalid")
        for value in (context.max_cost_micros, context.estimated_cost_micros_per_1000_tokens):
            if type(value) is not int or value < 0:
                raise ProviderInvocationBlocked("pi_budget_invalid")
        deadline = context.deadline_epoch_seconds
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ProviderInvocationBlocked("pi_deadline_required")
        if "expires_at" in context.authorization_envelope:
            expires = context.authorization_envelope["expires_at"]
            if type(expires) not in (int, float) or not math.isfinite(expires):
                raise ProviderInvocationBlocked("pi_deadline_required")
            deadline = min(deadline, expires)
        if deadline <= self._clock():
            raise ProviderInvocationBlocked("pi_deadline_expired")
        if (target.provider_id, target.model) != (context.selected_provider_id, context.selected_model_id):
            raise ProviderInvocationBlocked("pi_model_not_authorized")
        try:
            endpoint = normalize_provider_endpoint_identity(
                provider_id=target.provider_id, endpoint_url=target.base_url
            )
            if endpoint != context.provider_endpoint_identity or not endpoint.endswith("/chat/completions"):
                raise ValueError("endpoint mismatch")
            if is_forbidden_provider_endpoint_target(endpoint):
                raise ValueError("forbidden endpoint")
            local = is_local_provider_endpoint(
                provider_id=target.provider_id, endpoint_url=endpoint, endpoint_bound=True
            )
            if not local and context.external_egress_allowed is not True:
                raise ValueError("external egress denied")
            validate_provider_endpoint_resolution(
                provider_id=target.provider_id,
                endpoint_url=endpoint,
                endpoint_bound=True,
                resolver=self._resolver,
            )
        except ValueError as exc:
            raise ProviderInvocationBlocked("pi_endpoint_not_authorized") from exc
        return PiExecutionProjection(
            target=replace(target, base_url=endpoint.removesuffix("/chat/completions")),
            max_tokens=min(1024, context.max_completion_tokens_per_call),
            deadline=float(deadline),
        )

    def remaining_seconds(self, projection: PiExecutionProjection) -> float:
        return max(0.0, projection.deadline - self._clock())

    def reserve(self, projection: PiExecutionProjection, *, prompt: str, cwd: Path) -> None:
        # Exact pinned SDK custom-system framing, plus a conservative byte-based
        # estimate and chat framing margin. This is not tokenizer-exact usage.
        system = f"{PI_SYSTEM_PROMPT}\nCurrent working directory: {str(cwd).replace(chr(92), '/')}\n"
        estimated_prompt_tokens = len((system + prompt).encode("utf-8")) + 256
        reserved_tokens = estimated_prompt_tokens + projection.max_tokens
        if reserved_tokens > min(8192, self._context.max_total_tokens):
            raise ProviderInvocationBlocked("pi_prompt_budget_exceeded")
        if self.remaining_seconds(projection) <= 0:
            raise ProviderInvocationBlocked("pi_deadline_expired")
        with self._lock:
            if self._consumed:
                raise ProviderInvocationBlocked("pi_invocation_already_reserved")
            # Consume before contacting the port: an uncertain response may have
            # reserved Hub budget and must not trigger another attempt locally.
            self._consumed = True
        context = replace(self._context, max_completion_tokens_per_call=projection.max_tokens)
        try:
            decision = self._budget.reserve(
                context=context,
                estimated_prompt_tokens=estimated_prompt_tokens,
                reservation_id=context.provider_call_id,
            )
        except Exception as exc:
            raise ProviderInvocationBlocked("pi_hub_budget_unavailable") from exc
        if (
            not isinstance(decision, ProviderBudgetDecision)
            or decision.schema != PROVIDER_BUDGET_DECISION_SCHEMA
            or decision.allowed is not True
            or type(decision.reserved_tokens) is not int
            or decision.reserved_tokens < reserved_tokens
        ):
            raise ProviderInvocationBlocked("pi_hub_budget_denied")
        # No trustworthy tokenizer-specific usage is projected yet. Keep the
        # reservation (the existing port's reconcile(actual_total_tokens=None)
        # semantics); never refund unknown work after failure or cancellation.
