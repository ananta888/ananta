"""Authorized LM bridge used by optional DSPy adapters."""

from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from time import monotonic
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence

from agent.services.dspy_optimization_ports import AuthorizedLmPort
from ananta_contracts.dspy_optimization import OptimizationBudgets
from ananta_contracts.provider_execution import ProviderExecutionBinding


@dataclass(frozen=True, slots=True)
class LmUsage:
    input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    cache_hit: bool | None


@dataclass(frozen=True, slots=True)
class DspyPriceProfile:
    input_micros_per_million: int
    output_micros_per_million: int
    reasoning_micros_per_million: int = 0

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 10**12
            for value in asdict(self).values()
        ):
            raise ValueError("dspy_price_profile_invalid")

    def cost(self, usage: LmUsage) -> int:
        if self.input_micros_per_million and usage.input_tokens is None:
            raise DspyBudgetExceeded("dspy_usage_missing")
        if self.output_micros_per_million and usage.output_tokens is None:
            raise DspyBudgetExceeded("dspy_usage_missing")
        if self.reasoning_micros_per_million and usage.reasoning_tokens is None:
            raise DspyBudgetExceeded("dspy_usage_missing")
        numerator = (
            (usage.input_tokens or 0) * self.input_micros_per_million
            + (usage.output_tokens or 0) * self.output_micros_per_million
            + (usage.reasoning_tokens or 0) * self.reasoning_micros_per_million
        )
        return (numerator + 999_999) // 1_000_000


class DspyBudgetExceeded(RuntimeError):
    pass


class DspyRetryableProviderError(RuntimeError):
    """Explicit provider-adapter signal for a bounded, policy-owned retry."""


class DspyBudgetLedger:
    def __init__(self, budgets: OptimizationBudgets) -> None:
        self._budgets = budgets
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._recorded: dict[str, tuple[int, int]] = {}
        self._calls = 0
        self._tokens = 0
        self._cost_micros = 0
        self._role_calls: dict[str, int] = {}
        self._inflight = 0

    def reserve(self, request_id: str, *, role: str = "student") -> None:
        with self._lock:
            if request_id in self._seen:
                raise DspyBudgetExceeded("dspy_model_call_replay_denied")
            if self._calls + 1 > self._budgets.max_model_calls:
                raise DspyBudgetExceeded("dspy_model_call_budget_exhausted")
            if self._role_calls.get(role, 0) + 1 > self._budgets.max_role_calls:
                raise DspyBudgetExceeded("dspy_model_role_budget_exhausted")
            self._seen.add(request_id)
            self._calls += 1
            self._role_calls[role] = self._role_calls.get(role, 0) + 1

    @contextmanager
    def slot(self):
        with self._lock:
            if self._inflight >= self._budgets.max_concurrency:
                raise DspyBudgetExceeded("dspy_model_concurrency_exhausted")
            self._inflight += 1
        try:
            yield
        finally:
            with self._lock:
                self._inflight -= 1

    def record(self, request_id: str, *, total_tokens: int | None, cost_micros: int | None) -> None:
        if total_tokens is None or cost_micros is None:
            raise DspyBudgetExceeded("dspy_usage_missing")
        with self._lock:
            if request_id not in self._seen:
                raise DspyBudgetExceeded("dspy_usage_without_reservation")
            previous = self._recorded.get(request_id)
            if previous is not None:
                if previous != (total_tokens, cost_micros):
                    raise DspyBudgetExceeded("dspy_usage_replay_mismatch")
                return
            projected_tokens = self._tokens + total_tokens
            projected_cost = self._cost_micros + cost_micros
            if projected_tokens > self._budgets.max_tokens or projected_cost > self._budgets.max_cost_micros:
                raise DspyBudgetExceeded("dspy_usage_budget_exhausted")
            self._tokens = projected_tokens
            self._cost_micros = projected_cost
            self._recorded[request_id] = (total_tokens, cost_micros)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"model_calls": self._calls, "tokens": self._tokens, "cost_micros": self._cost_micros}

    def detailed_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "model_calls": self._calls,
                "tokens": self._tokens,
                "cost_micros": self._cost_micros,
                "role_calls": dict(sorted(self._role_calls.items())),
                "inflight": self._inflight,
            }

    @property
    def max_retries(self) -> int:
        return int(self._budgets.max_retries)


class AnantaBaseLmBridge:
    def __init__(
        self,
        port: AuthorizedLmPort,
        *,
        bindings: Mapping[str, Mapping[str, Any]],
        ledger: DspyBudgetLedger,
        run_id: str,
        attempt_id: str,
        audit_sink: Callable[[Mapping[str, Any]], None] | None = None,
        clock: Callable[[], float] = monotonic,
        price_profiles: Mapping[str, Mapping[str, int] | DspyPriceProfile] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> None:
        self._port = port
        self._bindings = {role: ProviderExecutionBinding.from_mapping(raw) for role, raw in bindings.items()}
        self._ledger = ledger
        self._run_id = run_id
        self._attempt_id = attempt_id
        self._audit_sink = audit_sink or (lambda _event: None)
        self._clock = clock
        self._prices = {
            key: value if isinstance(value, DspyPriceProfile) else DspyPriceProfile(**dict(value))
            for key, value in (price_profiles or {}).items()
        }
        self._cancelled = cancelled

    def complete(self, *, role: str, messages: Sequence[Mapping[str, str]], call_index: int) -> dict[str, Any]:
        binding = self._bindings.get(role)
        if binding is None:
            raise PermissionError("dspy_lm_role_not_authorized")
        if any(
            set(message) != {"role", "content"} or message["role"] not in {"system", "user", "assistant"}
            for message in messages
        ):
            raise ValueError("dspy_lm_messages_invalid")
        logical_request_id = hashlib.sha256(
            f"{self._run_id}\0{self._attempt_id}\0{role}\0{call_index}".encode()
        ).hexdigest()
        started = self._clock()
        retry_index = 0
        while True:
            if self._cancelled():
                raise DspyBudgetExceeded("dspy_model_call_cancelled")
            reservation_id = hashlib.sha256(f"{logical_request_id}\0{retry_index}".encode()).hexdigest()
            self._ledger.reserve(reservation_id, role=role)
            try:
                with self._ledger.slot():
                    response = dict(
                        self._port.complete(
                            binding=binding,
                            role=role,
                            messages=messages,
                            request_id=logical_request_id,
                        )
                    )
                break
            except DspyRetryableProviderError:
                if retry_index >= self._ledger.max_retries:
                    raise RuntimeError("dspy_provider_retry_exhausted") from None
                retry_index += 1
        if self._cancelled():
            raise DspyBudgetExceeded("dspy_model_call_cancelled")
        usage = _usage(response.get("usage"))
        price = self._prices.get(binding.binding_id)
        if price is None:
            raise DspyBudgetExceeded("dspy_price_profile_missing")
        ananta_cost_micros = price.cost(usage)
        self._ledger.record(
            reservation_id, total_tokens=usage.total_tokens, cost_micros=ananta_cost_micros
        )
        result = {
            "text": str(response.get("text") or ""),
            "finish_reason": str(response.get("finish_reason") or "unknown"),
            "usage": asdict(usage),
            "binding_id": binding.binding_id,
            "request_digest": logical_request_id,
            "cost_micros": ananta_cost_micros,
            "observed_provider_cost_micros": _optional_int(response.get("cost_micros")),
        }
        self._audit_sink(
            {
                "schema": "ananta.dspy-lm-call-audit.v1",
                "run_id": self._run_id,
                "attempt_id": self._attempt_id,
                "binding_id": binding.binding_id,
                "role": role,
                "request_digest": logical_request_id,
                "input_digest": hashlib.sha256(_bounded_messages(messages).encode()).hexdigest(),
                "output_digest": hashlib.sha256(result["text"].encode()).hexdigest(),
                "input_bytes": len(_bounded_messages(messages).encode()),
                "output_bytes": len(result["text"].encode()),
                "usage": result["usage"],
                "finish_reason": result["finish_reason"],
                "cost_micros": result["cost_micros"],
                "observed_provider_cost_micros": result["observed_provider_cost_micros"],
                "cache_hit": usage.cache_hit,
                "retry_count": retry_index,
                "latency_ms": max(0, int((self._clock() - started) * 1_000)),
                "rollout_id": f"call-{call_index}",
            }
        )
        return result


class DspyLmCompatibilityBridge:
    """Expose the authorized bridge through pinned legacy and typed DSPy seams."""

    def __init__(self, bridge: AnantaBaseLmBridge, *, role: str) -> None:
        self._bridge = bridge
        self._role = role
        self._lock = threading.Lock()
        self._call_index = 0

    def complete_legacy(
        self,
        *,
        prompt: str | None = None,
        messages: Sequence[Mapping[str, str]] | None = None,
        parameters: Mapping[str, Any] | None = None,
    ) -> SimpleNamespace:
        params = dict(parameters or {})
        if set(params) - {"temperature", "max_tokens"}:
            raise ValueError("dspy_lm_parameter_denied")
        normalized = _messages(prompt=prompt, messages=messages)
        with self._lock:
            call_index = self._call_index
            self._call_index += 1
        try:
            result = self._bridge.complete(role=self._role, messages=normalized, call_index=call_index)
        except (DspyBudgetExceeded, PermissionError, ValueError):
            raise
        except Exception:
            raise RuntimeError("dspy_provider_call_failed") from None
        message = SimpleNamespace(content=result["text"], reasoning_content=None, tool_calls=None)
        choice = SimpleNamespace(message=message, finish_reason=result["finish_reason"])
        usage = {key: value for key, value in result["usage"].items() if value is not None}
        return SimpleNamespace(
            choices=[choice],
            usage=usage,
            model=f"ananta/{result['binding_id']}",
            _hidden_params={"response_cost": None},
        )

    def complete_typed(self, request: Mapping[str, Any]) -> dict[str, Any]:
        if set(request) - {"prompt", "messages", "role", "parameters"} or request.get("role", self._role) != self._role:
            raise ValueError("dspy_typed_lm_request_invalid")
        response = self.complete_legacy(
            prompt=request.get("prompt"),
            messages=request.get("messages"),
            parameters=request.get("parameters"),
        )
        choice = response.choices[0]
        return {
            "text": choice.message.content,
            "finish_reason": choice.finish_reason,
            "usage": dict(response.usage),
            "model": response.model,
        }

    def build_pinned_dspy_lm(self) -> object:
        import dspy

        compatibility = self

        class BoundAnantaLm(dspy.BaseLM):
            def __init__(self) -> None:
                super().__init__(model="ananta/hub-bound", cache=False, temperature=0.0, max_tokens=4096)

            def forward(self, prompt=None, messages=None, **kwargs):
                return compatibility.complete_legacy(prompt=prompt, messages=messages, parameters=kwargs)

            async def aforward(self, prompt=None, messages=None, **kwargs):
                return self.forward(prompt=prompt, messages=messages, **kwargs)

            def copy(self, **kwargs):
                if set(kwargs) - {"rollout_id", "temperature", "max_tokens"}:
                    raise ValueError("dspy_lm_copy_parameter_denied")
                return self

        return BoundAnantaLm()


def _usage(raw: object) -> LmUsage:
    value = dict(raw) if isinstance(raw, Mapping) else {}
    return LmUsage(
        input_tokens=_optional_int(value.get("input_tokens")),
        output_tokens=_optional_int(value.get("output_tokens")),
        reasoning_tokens=_optional_int(value.get("reasoning_tokens")),
        total_tokens=_optional_int(value.get("total_tokens")),
        cache_hit=value.get("cache_hit") if isinstance(value.get("cache_hit"), bool) else None,
    )


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("dspy_usage_invalid")
    return value


def _messages(*, prompt: object, messages: Sequence[Mapping[str, str]] | None) -> tuple[dict[str, str], ...]:
    if messages is not None and prompt is not None and prompt != "":
        raise ValueError("dspy_lm_prompt_messages_conflict")
    if messages is None:
        if not isinstance(prompt, str) or not prompt or len(prompt) > 256_000:
            raise ValueError("dspy_lm_prompt_invalid")
        return ({"role": "user", "content": prompt},)
    normalized = tuple(dict(message) for message in messages)
    if not normalized or len(normalized) > 128:
        raise ValueError("dspy_lm_messages_invalid")
    return normalized


def _bounded_messages(messages: Sequence[Mapping[str, str]]) -> str:
    value = "\n".join(f"{item['role']}\0{item['content']}" for item in messages)
    if len(value.encode()) > 512_000:
        raise ValueError("dspy_lm_messages_too_large")
    return value


__all__ = [
    "AnantaBaseLmBridge",
    "DspyBudgetExceeded",
    "DspyBudgetLedger",
    "DspyLmCompatibilityBridge",
    "DspyPriceProfile",
    "DspyRetryableProviderError",
    "LmUsage",
]
