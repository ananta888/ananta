"""Harness Runtime with typed, evolvable extension points (SEH-003).

The runtime turns a :class:`HarnessGenome` into behaviour through small strategy
ports. Only the ports are evolvable; security and governance invariants (gates,
caller-authorized tool capability, evidence and promotion authority) live
*outside* the mutable surface and are enforced here:

- every genome gate reference must be a registered immutable gate;
- the effective tool allowlist is the intersection of the genome and the
  caller-authorized capability, so a genome can never widen its own rights;
- strategy families are closed: an unknown strategy id is rejected, never
  silently replaced.

The ``baseline`` genome reproduces today's Ananta behaviour exactly (single
agent, hub-gate verification, policy-gateway memory, no action fusion).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ananta_contracts.harness_genome import (
    AgentTopology,
    ContextPolicy,
    GatePolicy,
    HarnessGenome,
    MemoryPolicy,
    ModelRouting,
    PromptStrategy,
    ReducerPolicy,
    RetrievalPolicy,
    ToolPolicy,
    VerificationPolicy,
)

# Strategy families that may be evolved. Membership is closed on purpose.
STRATEGY_FAMILIES = ("context", "tools", "memory", "delegation", "verification", "stop_retry", "reducer")


class HarnessInvariantViolation(RuntimeError):
    """A genome tried to leave the immutable security/governance boundary."""


@dataclass(frozen=True, slots=True)
class ContextReservation:
    budget_tokens: int
    reserved_completion_tokens: int
    reserved_tool_tokens: int

    @property
    def available_input_tokens(self) -> int:
        return self.budget_tokens - self.reserved_completion_tokens - self.reserved_tool_tokens


@runtime_checkable
class ContextStrategyPort(Protocol):
    strategy_id: str

    def reservation(self, policy: ContextPolicy) -> ContextReservation: ...


@runtime_checkable
class ToolStrategyPort(Protocol):
    strategy_id: str

    def effective_tools(self, policy: ToolPolicy, authorized: frozenset[str]) -> tuple[str, ...]: ...


@runtime_checkable
class MemoryStrategyPort(Protocol):
    strategy_id: str


@runtime_checkable
class DelegationStrategyPort(Protocol):
    strategy_id: str


@runtime_checkable
class VerificationStrategyPort(Protocol):
    strategy_id: str


@runtime_checkable
class StopRetryStrategyPort(Protocol):
    strategy_id: str


@runtime_checkable
class ReducerStrategyPort(Protocol):
    strategy_id: str


class GateRegistryPort(Protocol):
    def is_registered(self, gate_ref: str) -> bool: ...


class StrategyRegistry:
    """Capability-driven selection of strategy implementations per family."""

    def __init__(self) -> None:
        self._families: dict[str, dict[str, Callable[[], object]]] = {family: {} for family in STRATEGY_FAMILIES}

    def register(self, family: str, strategy_id: str, factory: Callable[[], object]) -> None:
        if family not in self._families:
            raise ValueError(f"harness_strategy_family_unknown:{family}")
        if not isinstance(strategy_id, str) or not strategy_id or not callable(factory):
            raise ValueError("harness_strategy_registration_invalid")
        if strategy_id in self._families[family]:
            raise ValueError(f"harness_strategy_duplicate:{family}:{strategy_id}")
        self._families[family][strategy_id] = factory

    def known(self, family: str, strategy_id: str) -> bool:
        return strategy_id in self._families.get(family, {})

    def create(self, family: str, strategy_id: str) -> object:
        factory = self._families.get(family, {}).get(strategy_id)
        if factory is None:
            raise HarnessInvariantViolation(f"harness_strategy_unsupported:{family}:{strategy_id}")
        return factory()


def _default_context_strategy() -> ContextStrategyPort:
    @dataclass(frozen=True, slots=True)
    class _ReservationContext:
        strategy_id = "fixed_reservation"

        def reservation(self, policy: ContextPolicy) -> ContextReservation:
            return ContextReservation(policy.budget_tokens, policy.reserved_completion_tokens,
                                      policy.reserved_tool_tokens)

    return _ReservationContext()


def _default_tool_strategy() -> ToolStrategyPort:
    @dataclass(frozen=True, slots=True)
    class _ContractTools:
        strategy_id = "contract"

        def effective_tools(self, policy: ToolPolicy, authorized: frozenset[str]) -> tuple[str, ...]:
            return tuple(tool for tool in policy.allowed_tools if tool in authorized)

    return _ContractTools()


def build_default_strategy_registry() -> StrategyRegistry:
    """The non-evolvable baseline implementations behind every strategy family."""
    registry = StrategyRegistry()
    registry.register("context", "fixed_reservation", _default_context_strategy)
    registry.register("tools", "contract", _default_tool_strategy)
    for family, strategy_id in (("memory", "policy_gateway"), ("delegation", "hub_only"),
                                ("verification", "hub_gate"), ("stop_retry", "task_terminal"),
                                ("reducer", "none")):
        registry.register(family, strategy_id, lambda strategy_id=strategy_id: _StubStrategy(strategy_id))
    return registry


@dataclass(frozen=True, slots=True)
class _StubStrategy:
    strategy_id: str


@dataclass(frozen=True, slots=True)
class HarnessRuntimeDescriptor:
    genome_id: str
    strategies: Mapping[str, str]
    effective_tools: tuple[str, ...]
    immutable_gates: tuple[str, ...]
    promotion_ref: str


class HarnessRuntime:
    """A validated binding of one genome to concrete strategies and capabilities."""

    def __init__(self, genome: HarnessGenome, *, strategies: StrategyRegistry, gates: GateRegistryPort,
                 authorized_tools: frozenset[str] = frozenset()):
        genome.assert_valid()
        self._genome = genome
        self._strategies = strategies
        self._gates = gates
        self._authorized_tools = frozenset(authorized_tools)
        self._selected = self._select_strategies()
        self._effective_tools = self._validate()

    @property
    def genome(self) -> HarnessGenome:
        return self._genome

    def _select_strategies(self) -> dict[str, object]:
        return {
            "context": self._strategies.create("context", _context_id(self._genome.context)),
            "tools": self._strategies.create("tools", self._genome.tools.routing_heuristic),
            "memory": self._strategies.create("memory", self._genome.memory.recall_strategy),
            "delegation": self._strategies.create("delegation", _delegation_id(self._genome.topology)),
            "verification": self._strategies.create("verification", self._genome.verification.strategy),
            "stop_retry": self._strategies.create("stop_retry", self._genome.topology.termination),
            "reducer": self._strategies.create("reducer", self._genome.reducer.strategy),
        }

    def _validate(self) -> tuple[str, ...]:
        for gate_ref in self._genome.gates.gate_refs:
            if not self._gates.is_registered(gate_ref):
                raise HarnessInvariantViolation(f"harness_gate_unregistered:{gate_ref}")
        if not self._gates.is_registered(self._genome.gates.promotion_ref):
            raise HarnessInvariantViolation(f"harness_gate_unregistered:{self._genome.gates.promotion_ref}")
        tools = self._strategies.create("tools", self._genome.tools.routing_heuristic)
        effective = tuple(getattr(tools, "effective_tools")(self._genome.tools, self._authorized_tools))
        return effective

    def reservation(self) -> ContextReservation:
        return getattr(self._selected["context"], "reservation")(self._genome.context)

    def effective_tools(self) -> tuple[str, ...]:
        return self._effective_tools

    def strategy(self, family: str) -> object:
        if family not in self._selected:
            raise ValueError(f"harness_strategy_family_unknown:{family}")
        return self._selected[family]

    def describe(self) -> HarnessRuntimeDescriptor:
        return HarnessRuntimeDescriptor(
            genome_id=self._genome.genome_id,
            strategies={family: getattr(strategy, "strategy_id", "unknown")
                        for family, strategy in self._selected.items()},
            effective_tools=self._effective_tools,
            immutable_gates=self._genome.gates.gate_refs,
            promotion_ref=self._genome.gates.promotion_ref,
        )


def _context_id(policy: ContextPolicy) -> str:
    return "fixed_reservation"


def _delegation_id(topology: AgentTopology) -> str:
    return "hub_only" if topology.delegation_depth == 0 else "delegated"


def baseline_genome(*, code_hash: str, config_hash: str, provider_id: str, model_id: str,
                    gate_refs: tuple[str, ...], promotion_ref: str, budget_tokens: int = 32768,
                    reserved_completion_tokens: int = 2048, allowed_tools: tuple[str, ...] = ()) -> HarnessGenome:
    """The current Ananta behaviour as a genome: single agent, hub gate, no fusion."""
    return HarnessGenome(
        code_hash=code_hash, config_hash=config_hash,
        prompt=PromptStrategy(system_ref="prompt://ananta/system", user_ref="prompt://ananta/user"),
        context=ContextPolicy(budget_tokens=budget_tokens, reserved_completion_tokens=reserved_completion_tokens),
        tools=ToolPolicy(allowed_tools=allowed_tools, routing_heuristic="contract", action_fusion=False),
        model_routing=ModelRouting(provider_id=provider_id, model_id=model_id),
        gates=GatePolicy(gate_refs=gate_refs, promotion_ref=promotion_ref),
        memory=MemoryPolicy(), retrieval=RetrievalPolicy(), topology=AgentTopology(),
        reducer=ReducerPolicy(), verification=VerificationPolicy(),
    )
