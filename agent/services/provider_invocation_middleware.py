"""Provider-neutral redaction, egress, budget, cache, and retry middleware."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

from agent.providers.redaction import redact_provider_payload
from agent.services.token_budget_service import TokenBudgetService
from ananta_contracts.provider_invocation import (
    ProviderBudgetDecision,
    ProviderInvocationBlocked,
    ProviderInvocationContext,
)

PROVIDER_EVENT_SCHEMA = "ananta.provider_invocation_event.v1"


class ProviderBudgetPort(Protocol):
    def reserve(
        self,
        *,
        context: ProviderInvocationContext,
        estimated_prompt_tokens: int,
        reservation_id: str = "",
    ) -> ProviderBudgetDecision: ...

    def reconcile(
        self,
        *,
        context: ProviderInvocationContext,
        reserved_tokens: int,
        actual_total_tokens: int | None,
        reservation_id: str = "",
    ) -> None: ...


class ProviderRetryBudgetPort(Protocol):
    """Hub-owned combined retry decision; implementations may be HTTP adapters."""

    def consume(
        self,
        *,
        context: ProviderInvocationContext,
        retry_id: str,
        maximum: int,
    ) -> tuple[bool, str, int, int]: ...


class RetryBudgetOwnerProviderAdapter:
    """DIP adapter over the shared Hub RetryBudgetOwner contract."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def consume(
        self,
        *,
        context: ProviderInvocationContext,
        retry_id: str,
        maximum: int,
    ) -> tuple[bool, str, int, int]:
        try:
            snapshot = self._owner.consume_retry(
                tenant_id=context.tenant_id,
                run_id=context.run_id,
                retry_id=retry_id,
                category="provider",
                maximum=maximum,
            )
        except Exception as exc:  # The port exposes stable, provider-neutral denial codes.
            reason = str(exc) or "provider_combined_retry_budget_denied"
            return False, reason, 0, maximum
        return True, "provider_combined_retry_reserved", int(snapshot.used), int(snapshot.remaining)


@dataclass
class _BudgetUsage:
    attempts: int = 0
    tokens: int = 0
    cost_micros: int = 0


class AtomicProviderBudgetLedger:
    """Process-local reference ledger; persistent stores implement the same port."""

    def __init__(self) -> None:
        self._usage: dict[tuple[str, str, str], _BudgetUsage] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(context: ProviderInvocationContext) -> tuple[str, str, str]:
        return (context.tenant_id, context.run_id, context.policy_version)

    def reserve(
        self,
        *,
        context: ProviderInvocationContext,
        estimated_prompt_tokens: int,
        reservation_id: str = "",
    ) -> ProviderBudgetDecision:
        del reservation_id
        now = time.time()
        reserved_tokens = max(0, estimated_prompt_tokens) + context.max_completion_tokens_per_call
        reserved_cost = (
            (reserved_tokens * context.estimated_cost_micros_per_1000_tokens + 999) // 1000
            if context.estimated_cost_micros_per_1000_tokens
            else 0
        )
        with self._lock:
            usage = self._usage.setdefault(self._key(context), _BudgetUsage())
            if context.deadline_epoch_seconds is not None and now >= context.deadline_epoch_seconds:
                return ProviderBudgetDecision(
                    False, "provider_deadline_exceeded", usage.attempts, usage.tokens, usage.cost_micros
                )
            if usage.attempts >= context.max_attempts:
                return ProviderBudgetDecision(
                    False, "provider_retry_budget_exceeded", usage.attempts, usage.tokens, usage.cost_micros
                )
            if context.max_total_tokens and usage.tokens + reserved_tokens > context.max_total_tokens:
                return ProviderBudgetDecision(
                    False, "provider_token_budget_exceeded", usage.attempts, usage.tokens, usage.cost_micros
                )
            if context.max_cost_micros and usage.cost_micros + reserved_cost > context.max_cost_micros:
                return ProviderBudgetDecision(
                    False, "provider_cost_budget_exceeded", usage.attempts, usage.tokens, usage.cost_micros
                )
            usage.attempts += 1
            usage.tokens += reserved_tokens
            usage.cost_micros += reserved_cost
            return ProviderBudgetDecision(
                True, "provider_budget_reserved", usage.attempts, reserved_tokens, reserved_cost
            )

    def reconcile(
        self,
        *,
        context: ProviderInvocationContext,
        reserved_tokens: int,
        actual_total_tokens: int | None,
        reservation_id: str = "",
    ) -> None:
        del reservation_id
        if actual_total_tokens is None:
            return
        with self._lock:
            usage = self._usage.setdefault(self._key(context), _BudgetUsage())
            delta = int(actual_total_tokens) - int(reserved_tokens)
            usage.tokens = max(0, usage.tokens + delta)

    def snapshot(self, context: ProviderInvocationContext) -> dict[str, int]:
        with self._lock:
            usage = self._usage.get(self._key(context), _BudgetUsage())
            return {
                "attempts": usage.attempts,
                "tokens": usage.tokens,
                "cost_micros": usage.cost_micros,
            }


class ProviderCachePort(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...

    def put(self, key: str, value: dict[str, Any]) -> None: ...


class InMemoryProviderCache:
    def __init__(self, *, max_entries: int = 256) -> None:
        self._max_entries = max(1, int(max_entries))
        self._values: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            value = self._values.get(key)
            return copy.deepcopy(value) if value is not None else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            if key not in self._values and len(self._values) >= self._max_entries:
                oldest_key = next(iter(self._values))
                self._values.pop(oldest_key, None)
            self._values[key] = copy.deepcopy(value)


class ProviderEventPort(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...


class BoundedProviderEventSink:
    def __init__(self, *, max_events: int = 1000) -> None:
        self._max_events = max(1, int(max_events))
        self._events: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(copy.deepcopy(event))
            if len(self._events) > self._max_events:
                del self._events[: len(self._events) - self._max_events]

    def snapshot(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return tuple(copy.deepcopy(self._events))


@dataclass(frozen=True)
class PreparedProviderInvocation:
    context: ProviderInvocationContext
    payload: dict[str, Any]
    cache_key: str
    payload_hash: str
    reserved_tokens: int
    budget: ProviderBudgetDecision | None
    budget_reservation_id: str = ""
    uses_hub_budget: bool = False
    cached_response: dict[str, Any] | None = None


class ProviderInvocationMiddleware:
    def __init__(
        self,
        *,
        budgets: ProviderBudgetPort | None = None,
        cache: ProviderCachePort | None = None,
        events: ProviderEventPort | None = None,
        token_budget: TokenBudgetService | None = None,
        retry_budgets: ProviderRetryBudgetPort | None = None,
        hub_budgets: ProviderBudgetPort | None = None,
    ) -> None:
        self._budgets = budgets or AtomicProviderBudgetLedger()
        self._cache = cache or InMemoryProviderCache()
        self._events = events or BoundedProviderEventSink()
        self._token_budget = token_budget or TokenBudgetService()
        self._retry_budgets = retry_budgets
        self._hub_budgets = hub_budgets

    def prepare(
        self,
        *,
        context: ProviderInvocationContext | dict[str, Any] | None,
        provider: str,
        model: str,
        endpoint_url: str,
        payload: dict[str, Any],
    ) -> PreparedProviderInvocation:
        resolved = ProviderInvocationContext.from_value(context)
        resolved.assert_valid()
        if resolved.selected_provider_id and (
            provider != resolved.selected_provider_id or model != resolved.selected_model_id
        ):
            self._publish(
                resolved,
                "provider.selection.blocked",
                provider,
                model,
                "provider_selection_binding_mismatch",
            )
            raise ProviderInvocationBlocked("provider_selection_binding_mismatch")
        if resolved.retry_attempt > 0 and resolved.require_hub_retry_budget:
            if self._retry_budgets is None:
                raise ProviderInvocationBlocked("provider_combined_retry_budget_unavailable")
            allowed, reason, _used, _remaining = self._retry_budgets.consume(
                context=resolved,
                retry_id=resolved.retry_id,
                maximum=resolved.combined_retry_maximum,
            )
            self._publish(resolved, "provider.retry.checked", provider, model, reason)
            if not allowed:
                raise ProviderInvocationBlocked(reason or "provider_combined_retry_budget_denied")
        external = not self._is_local_endpoint(endpoint_url)
        if external and not resolved.external_egress_allowed:
            self._publish(resolved, "provider.egress.blocked", provider, model, "provider_egress_denied")
            raise ProviderInvocationBlocked("provider_egress_denied")

        redacted = redact_provider_payload(payload, secret_refs=resolved.secret_refs)
        canonical_payload = json.dumps(redacted, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        payload_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        cache_key = self.cache_key(
            context=resolved,
            provider=provider,
            model=model,
            payload_hash=payload_hash,
        )
        if resolved.cache_enabled:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._publish(resolved, "provider.cache.hit", provider, model, "provider_cache_hit")
                return PreparedProviderInvocation(
                    context=resolved,
                    payload=dict(redacted),
                    cache_key=cache_key,
                    payload_hash=payload_hash,
                    reserved_tokens=0,
                    budget=None,
                    budget_reservation_id="",
                    uses_hub_budget=False,
                    cached_response=cached,
                )

        estimate = self._token_budget.estimate(canonical_payload, provider=provider, model=model)
        estimated_tokens = int(estimate.get("tokens") or 0)
        uses_hub_budget = bool(resolved.require_hub_provider_budget)
        budget_port = self._hub_budgets if uses_hub_budget else self._budgets
        if budget_port is None:
            raise ProviderInvocationBlocked("provider_hub_budget_unavailable")
        reservation_id = self._reservation_id(
            context=resolved,
            provider=provider,
            model=model,
            payload_hash=payload_hash,
        )
        decision = budget_port.reserve(
            context=resolved,
            estimated_prompt_tokens=estimated_tokens,
            reservation_id=reservation_id,
        )
        if not decision.allowed:
            self._publish(resolved, "provider.budget.blocked", provider, model, decision.reason_code)
            raise ProviderInvocationBlocked(decision.reason_code)
        self._publish(resolved, "provider.call.authorized", provider, model, decision.reason_code)
        return PreparedProviderInvocation(
            context=resolved,
            payload=dict(redacted),
            cache_key=cache_key,
            payload_hash=payload_hash,
            reserved_tokens=decision.reserved_tokens,
            budget=decision,
            budget_reservation_id=reservation_id,
            uses_hub_budget=uses_hub_budget,
        )

    def complete(
        self,
        prepared: PreparedProviderInvocation,
        *,
        provider: str,
        model: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        raw_usage = response.get("usage")
        usage: dict[str, Any] = dict(raw_usage) if isinstance(raw_usage, dict) else {}
        normalized = self._token_budget.normalize(usage, provider=provider, model=model)
        actual_total = normalized.get("actual_total_tokens")
        budget_port = self._hub_budgets if prepared.uses_hub_budget else self._budgets
        if budget_port is None:
            raise ProviderInvocationBlocked("provider_hub_budget_unavailable")
        budget_port.reconcile(
            context=prepared.context,
            reserved_tokens=prepared.reserved_tokens,
            actual_total_tokens=int(actual_total) if isinstance(actual_total, int) else None,
            reservation_id=prepared.budget_reservation_id,
        )
        if prepared.context.cache_enabled:
            self._cache.put(prepared.cache_key, response)
        self._publish(prepared.context, "provider.call.completed", provider, model, "provider_call_completed")
        return {
            "schema": "ananta.provider_middleware_result.v1",
            "cache_key": prepared.cache_key,
            "payload_hash": prepared.payload_hash,
            "cache_hit": False,
            "usage": normalized,
            "budget": (
                {
                    "attempts": prepared.budget.attempts,
                    "reserved_tokens": prepared.budget.reserved_tokens,
                    "reserved_cost_micros": prepared.budget.reserved_cost_micros,
                }
                if prepared.budget is not None
                else None
            ),
        }

    def fail(
        self,
        prepared: PreparedProviderInvocation,
        *,
        provider: str,
        model: str,
        reason_code: str,
    ) -> None:
        self._publish(prepared.context, "provider.call.failed", provider, model, reason_code)

    @staticmethod
    def cache_key(
        *,
        context: ProviderInvocationContext,
        provider: str,
        model: str,
        payload_hash: str,
    ) -> str:
        binding = {
            "tenant_id": context.tenant_id,
            "policy_version": context.policy_version,
            "prompt_version": context.prompt_version,
            "provider": provider,
            "model": model,
            "payload_hash": payload_hash,
        }
        rendered = json.dumps(binding, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "provider-cache-" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    @staticmethod
    def _reservation_id(
        *,
        context: ProviderInvocationContext,
        provider: str,
        model: str,
        payload_hash: str,
    ) -> str:
        binding = {
            "tenant_id": context.tenant_id,
            "run_id": context.run_id,
            "policy_version": context.policy_version,
            "workflow_id": context.workflow_id,
            "step_id": context.step_id,
            "attempt_id": context.attempt_id,
            "retry_attempt": context.retry_attempt,
            "provider": provider,
            "model": model,
            "payload_hash": payload_hash,
        }
        rendered = json.dumps(
            binding,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return "provider-call-" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_local_endpoint(endpoint_url: str) -> bool:
        host = (urlparse(endpoint_url).hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}

    def _publish(
        self,
        context: ProviderInvocationContext,
        event_type: str,
        provider: str,
        model: str,
        reason_code: str,
    ) -> None:
        self._events.publish(
            {
                "schema": PROVIDER_EVENT_SCHEMA,
                "event_type": event_type,
                "tenant_id": context.tenant_id,
                "workflow_id": context.workflow_id,
                "run_id": context.run_id,
                "correlation_id": context.correlation_id,
                "policy_version": context.policy_version,
                "prompt_version": context.prompt_version,
                "provider": provider,
                "model": model,
                "reason_code": reason_code,
            }
        )


_DEFAULT_MIDDLEWARE: ProviderInvocationMiddleware | None = None
_DEFAULT_MIDDLEWARE_LOCK = threading.Lock()


def get_provider_invocation_middleware() -> ProviderInvocationMiddleware:
    """Return the process-local adapter composition shared by all LLM entrypoints.

    The singleton is a composition detail, not a provider registry.  Its ports
    remain injectable in tests and production can replace the entire service
    before startup.  Sharing it prevents legacy ``generate_text`` and the
    newer model facade from maintaining independent budget/cache decisions.
    """

    global _DEFAULT_MIDDLEWARE
    if _DEFAULT_MIDDLEWARE is not None:
        return _DEFAULT_MIDDLEWARE
    with _DEFAULT_MIDDLEWARE_LOCK:
        if _DEFAULT_MIDDLEWARE is None:
            retry_budgets = None
            try:
                from worker.runtime.workflow_hub_gateway import (
                    HttpWorkflowHubDecisionClient,
                    HubProviderBudgetAdapter,
                    HubProviderRetryBudgetAdapter,
                )

                client = HttpWorkflowHubDecisionClient.from_environment()
                if client is not None:
                    retry_budgets = HubProviderRetryBudgetAdapter(client)
                    hub_budgets = HubProviderBudgetAdapter(client)
                else:
                    hub_budgets = None
            except (ImportError, ValueError):
                # A Hub-bound retry still fails closed in ``prepare`` when no
                # valid port exists; legacy/non-workflow provider calls remain
                # available during incremental rollout.
                retry_budgets = None
                hub_budgets = None
            _DEFAULT_MIDDLEWARE = ProviderInvocationMiddleware(
                retry_budgets=retry_budgets,
                hub_budgets=hub_budgets,
            )
    return _DEFAULT_MIDDLEWARE


def reset_provider_invocation_middleware() -> None:
    """Test/process lifecycle hook; never called from request handling."""

    global _DEFAULT_MIDDLEWARE
    with _DEFAULT_MIDDLEWARE_LOCK:
        _DEFAULT_MIDDLEWARE = None


__all__ = [
    "AtomicProviderBudgetLedger",
    "BoundedProviderEventSink",
    "InMemoryProviderCache",
    "PreparedProviderInvocation",
    "ProviderBudgetDecision",
    "ProviderBudgetPort",
    "ProviderCachePort",
    "ProviderEventPort",
    "ProviderInvocationBlocked",
    "ProviderInvocationContext",
    "ProviderInvocationMiddleware",
    "ProviderRetryBudgetPort",
    "RetryBudgetOwnerProviderAdapter",
    "get_provider_invocation_middleware",
    "reset_provider_invocation_middleware",
]
