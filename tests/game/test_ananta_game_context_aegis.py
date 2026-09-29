from __future__ import annotations

from agent.game.context_aegis import ContextAegis
from agent.game.models import AgentUnit, ContextGate


def test_context_aegis_allows_explicitly_allowed_local_role() -> None:
    decision = ContextAegis().decide(
        agent=AgentUnit(id="agent:local", role="local_worker"),
        gate=ContextGate(
            id="gate:1",
            territory_id="territory:agent/services",
            visibility="allow",
            allowed_roles=("local_worker",),
        ),
    )
    assert decision.decision == "allow"
    assert decision.visibility == "visible"


def test_context_aegis_denies_role_not_allowed_as_hidden() -> None:
    decision = ContextAegis().decide(
        agent=AgentUnit(id="agent:cloud", role="cloud_worker"),
        gate=ContextGate(
            id="gate:2",
            territory_id="territory:agent/services",
            visibility="allow",
            allowed_roles=("local_worker",),
        ),
    )
    assert decision.decision == "deny"
    assert decision.visibility == "hidden"


def test_context_aegis_redacts_secret_territories() -> None:
    decision = ContextAegis().decide(
        agent=AgentUnit(id="agent:local", role="local_worker"),
        gate=ContextGate(
            id="gate:3",
            territory_id="territory:secrets",
            visibility="allow",
            secret=True,
        ),
    )
    assert decision.decision == "redacted"
    assert decision.visibility == "redacted"


def test_context_aegis_cloud_denied_for_local_only() -> None:
    decision = ContextAegis().decide(
        agent=AgentUnit(id="agent:cloud", role="cloud_worker"),
        gate=ContextGate(
            id="gate:4",
            territory_id="territory:agent/services",
            visibility="allow",
            local_only=True,
        ),
    )
    assert decision.decision == "deny"
    assert decision.reason_code == "local_only_territory_denied"


def test_context_aegis_unknown_territory_defaults_to_hidden_deny() -> None:
    decision = ContextAegis().decide(agent=AgentUnit(id="agent:local", role="local_worker"), gate=None)
    assert decision.decision == "deny"
    assert decision.visibility == "hidden"
