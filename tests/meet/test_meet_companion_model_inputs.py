"""Exact SQL/Registry tool receipts, synthetic Task/MCP admission fixtures."""

from copy import deepcopy

import pytest
from sqlalchemy import select, update

from agent.db_models.evidence_identity import HubRunEvidenceIdentityDB, HubSourceEvidenceIdentityDB
from agent.repositories.meet_companion_tool_sources import sources
from agent.services.meet_companion_model_inputs import MeetCompanionModelInputs
from agent.services.meet_companion_task_grants import closed_companion_assignment
from agent.services.meet_companion_task_tools import canonical, digest
from agent.services.meet_contract import MeetError
from tests.meet.test_meet_companion_task_grants import assigned as assigned
from tests.meet.test_meet_companion_task_tools import hub as hub
from tests.meet.test_meet_companion_task_tools import post


def verify(f, responses):
    service = MeetCompanionModelInputs(f.registry, f.store, f.policy)
    scope = f.f.authority.current("task", "dispatch", "runtime")
    with f.app.app_context():
        return service.verify(scope, closed_companion_assignment(f.request["assignment"]), responses)


def result(f):
    response = post(f)[1]
    assert response.status_code == 200
    return response.get_json()


def test_exact_completed_tool_sources_are_verified_without_issuing_model_run(hub):
    response = result(hub)
    assert verify(hub, [response]) == response["sources"]
    assert verify(hub, []) == []
    with hub.database.connect() as connection:
        assert len(connection.execute(select(HubRunEvidenceIdentityDB.__table__)).all()) == 1
        assert len(connection.execute(select(HubSourceEvidenceIdentityDB.__table__)).all()) == 2


@pytest.mark.parametrize("mutation", ["text", "lease", "source", "run", "repeated", "nonobject"])
def test_changed_unknown_or_replayed_evidence_cannot_supply_model_inputs(hub, mutation):
    response = deepcopy(result(hub))
    values = [response]
    if mutation == "text":
        response["text"] = "model replacement"
    elif mutation == "lease":
        response["run"]["dispatch_lease_id"] = "foreign"
    elif mutation == "source":
        response["sources"][0]["binding_digest"] = "f" * 64
    elif mutation == "run":
        # Explicit invalid identity fixture, never accepted evidence.
        response["run"]["run_id"] = "RUN_unknown_refusal_fixture"
    elif mutation == "repeated":
        values.append(response)
    else:
        values = [None]
    with pytest.raises(MeetError, match="tool_inputs_unverified"):
        verify(hub, values)


@pytest.mark.parametrize("withdrawal", ["revision", "paths", "chat_revision", "chat_pause"])
def test_current_source_or_chat_withdrawal_rejects_previously_successful_receipt(hub, withdrawal):
    response = result(hub)
    if withdrawal in {"revision", "paths"}:
        capability = dict(hub.capability)
        capability["revision" if withdrawal == "revision" else "allowed_paths"] = (
            "2" * 40 if withdrawal == "revision" else ["other/"]
        )
        hub.resolver.resolve.side_effect = lambda **kwargs: capability
    elif withdrawal == "chat_revision":
        hub.f.context["controls"]["chat"]["revision"] += 1
    else:
        hub.f.context["controls"]["chat"]["enabled"] = False
    with pytest.raises(MeetError):
        verify(hub, [response])


def test_mutated_admitted_source_bytes_cannot_supply_model_inputs(hub):
    response = result(hub)
    with hub.database.begin() as connection:
        connection.execute(update(sources).values(content=b'{"changed":true}'))
    with pytest.raises(MeetError, match="source_unavailable_or_mutated"):
        verify(hub, [response])


def test_mutated_registry_origin_cannot_supply_model_inputs(hub):
    response = result(hub)
    with hub.database.begin() as connection:
        connection.execute(update(HubSourceEvidenceIdentityDB.__table__).values(origin_type="other_domain"))
    with pytest.raises(MeetError, match="tool_inputs_unverified"):
        verify(hub, [response])


def test_foreign_tenant_cannot_read_immutable_tool_content(hub):
    response = result(hub)
    source = hub.registry.require_source_identity(tenant_id="tenant", project_id="project",
        source_id=response["sources"][0]["source_id"],
        expected_binding_digest=response["sources"][0]["binding_digest"])
    with pytest.raises(MeetError, match="source_unavailable_or_mutated"):
        hub.store.read("foreign", "project", source.content_digest)


def test_registered_identity_from_other_domain_is_not_a_tool_source(hub):
    response = result(hub)
    output = {key: response[key] for key in ("text", "truncated")}
    content = hub.store.admit("tenant", "project", canonical(output))
    unrelated = hub.registry.register_source(
        tenant_id="tenant", project_id="project", origin_type="synthetic_other_domain",
        origin_digest=digest({"fixture": "other-domain"}), content_digest=content,
        policy_digest=digest({"fixture": "other-policy"}), evidence_scope="test", synthetic=True,
    )
    response["sources"] = [{"source_id": unrelated.source_id, "binding_digest": unrelated.binding_digest}]
    # Explicit privileged SQL corruption fixture: even a matching result
    # digest cannot turn this independently registered origin into tool evidence.
    with hub.database.begin() as connection:
        connection.execute(update(HubRunEvidenceIdentityDB.__table__).values(result_digest=digest(response)))
    with pytest.raises(MeetError, match="tool_inputs_unverified"):
        verify(hub, [response])


def test_non_successful_registry_run_cannot_supply_model_inputs(hub):
    response = result(hub)
    with hub.database.begin() as connection:
        connection.execute(update(HubRunEvidenceIdentityDB.__table__).values(state="failed"))
    with pytest.raises(MeetError, match="tool_inputs_unverified"):
        verify(hub, [response])
