"""Authorized LM bridge used by optional DSPy adapters."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence

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


class DspyBudgetExceeded(RuntimeError):
    pass


class DspyBudgetLedger:
    def __init__(self, budgets: OptimizationBudgets) -> None:
        self._budgets = budgets
        self._lock = threading.Lock()
        self._seen: set[str] = set()
        self._recorded: dict[str, tuple[int, int]] = {}
        self._calls = 0
        self._tokens = 0
        self._cost_micros = 0

    def reserve(self, request_id: str) -> None:
        with self._lock:
            if request_id in self._seen:
                raise DspyBudgetExceeded("dspy_model_call_replay_denied")
            if self._calls + 1 > self._budgets.max_model_calls:
                raise DspyBudgetExceeded("dspy_model_call_budget_exhausted")
            self._seen.add(request_id)
            self._calls += 1

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


class AnantaBaseLmBridge:
    def __init__(
        self,
        port: AuthorizedLmPort,
        *,
        bindings: Mapping[str, Mapping[str, Any]],
        ledger: DspyBudgetLedger,
        run_id: str,
        attempt_id: str,
    ) -> None:
        self._port = port
        self._bindings = {role: ProviderExecutionBinding.from_mapping(raw) for role, raw in bindings.items()}
        self._ledger = ledger
        self._run_id = run_id
        self._attempt_id = attempt_id

    def complete(self, *, role: str, messages: Sequence[Mapping[str, str]], call_index: int) -> dict[str, Any]:
        binding = self._bindings.get(role)
        if binding is None:
            raise PermissionError("dspy_lm_role_not_authorized")
        if any(
            set(message) != {"role", "content"} or message["role"] not in {"system", "user", "assistant"}
            for message in messages
        ):
            raise ValueError("dspy_lm_messages_invalid")
        request_id = hashlib.sha256(f"{self._run_id}\0{self._attempt_id}\0{role}\0{call_index}".encode()).hexdigest()
        self._ledger.reserve(request_id)
        response = dict(self._port.complete(binding=binding, role=role, messages=messages, request_id=request_id))
        usage = _usage(response.get("usage"))
        self._ledger.record(
            request_id, total_tokens=usage.total_tokens, cost_micros=_optional_int(response.get("cost_micros"))
        )
        return {
            "text": str(response.get("text") or ""),
            "finish_reason": str(response.get("finish_reason") or "unknown"),
            "usage": asdict(usage),
            "binding_id": binding.binding_id,
            "request_digest": request_id,
        }


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


__all__ = ["AnantaBaseLmBridge", "DspyBudgetExceeded", "DspyBudgetLedger", "LmUsage"]
