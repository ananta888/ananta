"""HeuristicProposalValidator — validates HeuristicProposals before activation.

Validates:
  1. JSON schema (schemas/heuristic/heuristic_proposal.v1.json)
  2. Capability boundaries (T01.05 snake/chat forbidden caps)
  3. TTL policy (WorkerRoleConfig domain bounds)
  4. base_heuristic_ref exists in registry as active or deprecated

reason_codes: schema_invalid, capability_violation:<cap>, ttl_out_of_range, base_heuristic_not_found
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from agent.repositories.heuristic_lease_repo import _DOMAIN_TTL_DEFAULTS
from agent.services.heuristic_runtime.heuristic_registry_service import (
    HeuristicRegistry,
    _SNAKE_FORBIDDEN_CAPS,
    _CHAT_ALLOWED_CAPS,
    get_heuristic_registry,
)


@dataclass
class HeuristicProposal:
    proposal_id: str
    proposed_by: str
    domain: str
    strategy_kind: str
    description: str
    capabilities: list[str]
    requested_ttl_seconds: float
    safety_class: str = "bounded"
    deterministic: bool = True
    base_heuristic_ref: str | None = None
    simulation_result: dict[str, Any] | None = None
    human_approval_ref: str | None = None
    version: str = "1.0.0"
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposed_by": self.proposed_by,
            "domain": self.domain,
            "strategy_kind": self.strategy_kind,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "requested_ttl_seconds": self.requested_ttl_seconds,
            "safety_class": self.safety_class,
            "deterministic": self.deterministic,
            "base_heuristic_ref": self.base_heuristic_ref,
            "simulation_result": self.simulation_result,
            "human_approval_ref": self.human_approval_ref,
            "version": self.version,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "parameters": dict(self.parameters),
        }


@dataclass
class ValidationResult:
    passed: bool
    reason_codes: list[str] = field(default_factory=list)
    blocked_capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason_codes": list(self.reason_codes),
            "blocked_capabilities": list(self.blocked_capabilities),
        }


def _load_schema() -> dict[str, Any] | None:
    here = os.path.dirname(__file__)
    schema_path = os.path.normpath(
        os.path.join(here, "..", "..", "..", "schemas", "heuristic", "heuristic_proposal.v1.json")
    )
    try:
        with open(schema_path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


_REQUIRED_FIELDS = {"proposal_id", "proposed_by", "domain", "strategy_kind", "description",
                    "capabilities", "requested_ttl_seconds"}
_VALID_DOMAINS = {"tui_snake", "eclipse_snake", "chat_codecompass"}
_VALID_PROPOSERS = {"ananta-worker", "operator", "system"}


class HeuristicProposalValidator:
    def __init__(self, registry: HeuristicRegistry | None = None) -> None:
        self._registry = registry or get_heuristic_registry()

    def validate(self, proposal: HeuristicProposal) -> ValidationResult:
        reason_codes: list[str] = []
        blocked_caps: list[str] = []

        # 1 — Basic schema validation (field presence + enum values)
        d = proposal.to_dict()
        missing = _REQUIRED_FIELDS - set(d.keys())
        if missing:
            reason_codes.append(f"schema_invalid:missing_fields:{','.join(sorted(missing))}")

        if proposal.domain not in _VALID_DOMAINS:
            reason_codes.append(f"schema_invalid:invalid_domain:{proposal.domain}")

        if proposal.proposed_by not in _VALID_PROPOSERS:
            reason_codes.append(f"schema_invalid:invalid_proposer:{proposal.proposed_by}")

        if not (1.0 <= proposal.requested_ttl_seconds <= 60.0):
            reason_codes.append("schema_invalid:requested_ttl_seconds_out_of_schema_range")

        if not proposal.description.strip():
            reason_codes.append("schema_invalid:empty_description")

        # 2 — Capability boundaries
        caps = set(proposal.capabilities)
        domain = proposal.domain

        if domain in ("tui_snake", "eclipse_snake"):
            forbidden = caps & _SNAKE_FORBIDDEN_CAPS
            for cap in sorted(forbidden):
                reason_codes.append(f"capability_violation:{cap}")
                blocked_caps.append(cap)
            if not proposal.deterministic:
                reason_codes.append("capability_violation:non_deterministic_not_allowed_for_snake")

        elif domain == "chat_codecompass":
            if proposal.safety_class != "elevated":
                forbidden = caps - _CHAT_ALLOWED_CAPS - frozenset({"write_local_notes"})
                for cap in sorted(forbidden):
                    reason_codes.append(f"capability_violation:{cap}")
                    blocked_caps.append(cap)

        # 3 — TTL policy (bounds per domain, independent of _DOMAIN_TTL_DEFAULTS float)
        _TTL_BOUNDS: dict[str, tuple[float, float]] = {
            "tui_snake":        (5.0, 10.0),
            "eclipse_snake":    (5.0, 10.0),
            "chat_codecompass": (10.0, 20.0),
        }
        ttl_min, ttl_max = _TTL_BOUNDS.get(domain, (1.0, 60.0))
        if not (ttl_min <= proposal.requested_ttl_seconds <= ttl_max):
            reason_codes.append(
                f"ttl_out_of_range:{proposal.requested_ttl_seconds:.1f}s "
                f"(allowed {ttl_min}–{ttl_max})"
            )

        # 4 — base_heuristic_ref must exist in registry (active or deprecated)
        if proposal.base_heuristic_ref:
            found = False
            for h in self._registry.list_all():
                if h.heuristic_id == proposal.base_heuristic_ref:
                    if h.status in ("active", "deprecated"):
                        found = True
                    break
            if not found:
                reason_codes.append(f"base_heuristic_not_found:{proposal.base_heuristic_ref}")

        passed = len(reason_codes) == 0
        return ValidationResult(passed=passed, reason_codes=reason_codes, blocked_capabilities=blocked_caps)
