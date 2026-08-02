"""Pure effective-policy resolver for organization-bound assignments.

The Hub supplies every layer and remains the authority. Prompt content is not
an input, so it cannot add capabilities, tools, context, or evidence access.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Iterable

_EVIDENCE_ID_RE = re.compile(r"^(?:SRC|RUN)_[A-Za-z0-9][A-Za-z0-9_.:-]{0,126}$")


@dataclass(frozen=True, slots=True)
class OrganizationPolicyLayer:
    scope_id: str
    allowed_capabilities: frozenset[str] | None = None
    allowed_tools: frozenset[str] | None = None
    allowed_context_scopes: frozenset[str] | None = None
    allowed_evidence_ids: frozenset[str] | None = None
    denied_capabilities: frozenset[str] = field(default_factory=frozenset)
    denied_tools: frozenset[str] = field(default_factory=frozenset)
    denied_context_scopes: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class OrganizationEffectivePolicy:
    allowed_capabilities: frozenset[str]
    allowed_tools: frozenset[str]
    allowed_context_scopes: frozenset[str]
    allowed_evidence_ids: frozenset[str]
    denied: tuple[str, ...]
    missing: tuple[str, ...]
    policy_hash: str

    @property
    def allowed(self) -> bool:
        return not self.denied and not self.missing


class OrganizationEffectivePolicyService:
    """Resolve governance ∩ organization ∩ team ∩ slot ∩ task rights."""

    def __init__(
        self,
        *,
        known_capabilities: Iterable[str],
        known_tools: Iterable[str],
        known_context_scopes: Iterable[str],
    ) -> None:
        self._known_capabilities = _normalized_set(known_capabilities)
        self._known_tools = _normalized_set(known_tools)
        self._known_context_scopes = _normalized_set(known_context_scopes)

    def resolve(
        self,
        *,
        layers: Iterable[OrganizationPolicyLayer],
        required_capabilities: Iterable[str] = (),
        required_tools: Iterable[str] = (),
        required_context_scopes: Iterable[str] = (),
        requested_evidence_ids: Iterable[str] = (),
    ) -> OrganizationEffectivePolicy:
        ordered_layers = tuple(layers)
        if not ordered_layers:
            return self._decision(
                capabilities=frozenset(),
                tools=frozenset(),
                context=frozenset(),
                evidence=frozenset(),
                denied=("effective_policy_layers_missing",),
                missing=(),
                layers=(),
            )

        denied: list[str] = []
        for layer in ordered_layers:
            if not str(layer.scope_id or "").strip():
                denied.append("policy_layer_scope_id_missing")

        capabilities = self._intersection(
            ordered_layers,
            "allowed_capabilities",
            self._known_capabilities,
            "unknown_capability",
            denied,
        )
        tools = self._intersection(
            ordered_layers,
            "allowed_tools",
            self._known_tools,
            "unknown_tool",
            denied,
        )
        context = self._intersection(
            ordered_layers,
            "allowed_context_scopes",
            self._known_context_scopes,
            "unknown_context_scope",
            denied,
        )
        evidence = self._evidence_intersection(ordered_layers, denied)

        capabilities -= frozenset().union(*(layer.denied_capabilities for layer in ordered_layers))
        tools -= frozenset().union(*(layer.denied_tools for layer in ordered_layers))
        context -= frozenset().union(*(layer.denied_context_scopes for layer in ordered_layers))

        missing: list[str] = []
        _record_missing(missing, "capability", required_capabilities, capabilities, self._known_capabilities)
        _record_missing(missing, "tool", required_tools, tools, self._known_tools)
        _record_missing(missing, "context_scope", required_context_scopes, context, self._known_context_scopes)
        for evidence_id in sorted(_normalized_set(requested_evidence_ids)):
            if not _EVIDENCE_ID_RE.fullmatch(evidence_id):
                denied.append(f"evidence_id_invalid:{evidence_id}")
            elif evidence_id not in evidence:
                missing.append(f"evidence:{evidence_id}")

        return self._decision(
            capabilities=capabilities,
            tools=tools,
            context=context,
            evidence=evidence,
            denied=tuple(sorted(set(denied))),
            missing=tuple(sorted(set(missing))),
            layers=ordered_layers,
        )

    @staticmethod
    def _intersection(
        layers: tuple[OrganizationPolicyLayer, ...],
        field_name: str,
        known: frozenset[str],
        unknown_reason: str,
        denied: list[str],
    ) -> frozenset[str]:
        restrictions: list[frozenset[str]] = []
        for layer in layers:
            value = getattr(layer, field_name)
            if value is None:
                continue
            normalized = _normalized_set(value)
            unknown = normalized - known
            denied.extend(f"{unknown_reason}:{item}" for item in sorted(unknown))
            restrictions.append(normalized & known)
        if not restrictions:
            return frozenset()
        return frozenset.intersection(*restrictions)

    @staticmethod
    def _evidence_intersection(
        layers: tuple[OrganizationPolicyLayer, ...],
        denied: list[str],
    ) -> frozenset[str]:
        restrictions: list[frozenset[str]] = []
        for layer in layers:
            if layer.allowed_evidence_ids is None:
                continue
            normalized = _normalized_set(layer.allowed_evidence_ids)
            invalid = {item for item in normalized if not _EVIDENCE_ID_RE.fullmatch(item)}
            denied.extend(f"evidence_id_invalid:{item}" for item in sorted(invalid))
            restrictions.append(normalized - invalid)
        return frozenset.intersection(*restrictions) if restrictions else frozenset()

    @staticmethod
    def _decision(
        *,
        capabilities: frozenset[str],
        tools: frozenset[str],
        context: frozenset[str],
        evidence: frozenset[str],
        denied: tuple[str, ...],
        missing: tuple[str, ...],
        layers: tuple[OrganizationPolicyLayer, ...],
    ) -> OrganizationEffectivePolicy:
        digest_payload = {
            "allowed_capabilities": sorted(capabilities),
            "allowed_tools": sorted(tools),
            "allowed_context_scopes": sorted(context),
            "allowed_evidence_ids": sorted(evidence),
            "denied": list(denied),
            "missing": list(missing),
            "layer_scope_ids": [layer.scope_id for layer in layers],
        }
        policy_hash = hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return OrganizationEffectivePolicy(
            allowed_capabilities=capabilities,
            allowed_tools=tools,
            allowed_context_scopes=context,
            allowed_evidence_ids=evidence,
            denied=denied,
            missing=missing,
            policy_hash=policy_hash,
        )


def _normalized_set(values: Iterable[str]) -> frozenset[str]:
    return frozenset(str(value or "").strip() for value in values if str(value or "").strip())


def _record_missing(
    result: list[str],
    kind: str,
    requested: Iterable[str],
    allowed: frozenset[str],
    known: frozenset[str],
) -> None:
    for item in sorted(_normalized_set(requested)):
        if item not in known:
            result.append(f"unknown_{kind}:{item}")
        elif item not in allowed:
            result.append(f"{kind}:{item}")


__all__ = [
    "OrganizationEffectivePolicy",
    "OrganizationEffectivePolicyService",
    "OrganizationPolicyLayer",
]
