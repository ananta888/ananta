from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import AgentUnit, ContextGate


@dataclass(frozen=True)
class ContextDecision:
    territory_id: str
    decision: str
    visibility: str
    reason_code: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class ContextAegis:
    def decide(self, *, agent: AgentUnit, gate: ContextGate | None) -> ContextDecision:
        if gate is None:
            return ContextDecision(
                territory_id="unknown",
                decision="deny",
                visibility="hidden",
                reason_code="unknown_territory_default_deny",
            )

        role = str(agent.role or "").strip().lower()
        if gate.secret:
            return ContextDecision(
                territory_id=gate.territory_id,
                decision="redacted",
                visibility="redacted",
                reason_code="secret_territory_redacted",
            )
        if gate.local_only and role.startswith("cloud"):
            return ContextDecision(
                territory_id=gate.territory_id,
                decision="deny",
                visibility="hidden",
                reason_code="local_only_territory_denied",
            )
        if gate.allowed_roles and role not in {item.strip().lower() for item in gate.allowed_roles}:
            return ContextDecision(
                territory_id=gate.territory_id,
                decision="deny",
                visibility="hidden",
                reason_code="role_not_allowed",
            )

        visibility = str(gate.visibility or "hidden").strip().lower()
        if visibility in {"allow", "visible"}:
            return ContextDecision(
                territory_id=gate.territory_id,
                decision="allow",
                visibility="visible",
                reason_code="explicit_allow",
            )
        if visibility == "redacted":
            return ContextDecision(
                territory_id=gate.territory_id,
                decision="redacted",
                visibility="redacted",
                reason_code="policy_redacted",
            )
        return ContextDecision(
            territory_id=gate.territory_id,
            decision="deny",
            visibility="hidden",
            reason_code="default_hidden_deny",
        )
