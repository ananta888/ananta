"""WCRB-009: a task's signed CodeCompass capability travels Hub -> worker -> tool loop, and nowhere else."""

from types import SimpleNamespace

import pytest

from agent.cli_backends import tool_loop
from agent.services import _task_scoped_forwarding as forwarding
from agent.services import codecompass_task_capability as tc
from agent.services.access_roles import BUILTIN_ROLES

pytestmark = pytest.mark.timeout(30)
ROLES = {role_id: spec["grants"] for role_id, spec in BUILTIN_ROLES.items()}
CAP = {"subject_id": "anna", "capability_signature": "sig"}


@pytest.mark.parametrize("role, service, expected", [
    ("worker", True, CAP), ("worker", False, None), ("hub", True, None), ("hub", False, None)])
def test_only_a_worker_accepts_a_capability_and_only_from_the_hub(role, service, expected):
    assert tc.accepted_capability(CAP, role=role, service_authenticated=service) == expected
    assert tc.accepted_capability("x", role="worker", service_authenticated=True) is None


def test_the_capability_lives_for_one_step_only():
    assert tc.current_task_capability() is None
    with tc.task_capability_scope(CAP):
        assert tc.current_task_capability() == CAP
    assert tc.current_task_capability() is None


def test_access_mode_defaults_to_delegated():
    assert tc.access_mode({}) == "delegated" and tc.access_mode({"codecompass_access": "HUB"}) == "hub"
    assert tc.access_mode({"codecompass_access": "nonsense"}) == "delegated"


def test_the_capability_is_issued_for_the_requester_found_through_the_parents(monkeypatch):
    issued = []
    monkeypatch.setattr("agent.services.codecompass_capability_issuer.issue_capability",
                        lambda **kwargs: issued.append(kwargs) or {"issued": True})
    tasks = {"root": SimpleNamespace(id="root", requested_by_subject="anna", requested_by_tenant="t1",
                                     requested_roles=["viewer"], parent_task_id=None)}
    child = {"id": "child", "parent_task_id": "root", "tenant_id": "t9"}
    assert tc.issue_task_capability(child, audience="http://worker-a", get_task=tasks.get, role_grants=ROLES)
    call = issued[0]
    assert call["subject_id"] == "anna" and call["tenant_id"] == "t1" and call["task_id"] == "child"
    assert call["audience"] == "http://worker-a" and call["grants"].roles == {"viewer"}
    # system work without a requester runs unrestricted, under a system subject
    tc.issue_task_capability({"id": "sys", "tenant_id": "t9"}, audience="w", get_task=tasks.get, role_grants=ROLES)
    assert issued[1]["subject_id"] == "system:sys" and issued[1]["grants"] is None


def test_no_index_means_no_capability(monkeypatch):
    from agent.services.codecompass_capability_issuer import CapabilityError

    def fail(**_kwargs):
        raise CapabilityError("capability_index_unavailable")

    monkeypatch.setattr("agent.services.codecompass_capability_issuer.issue_capability", fail)
    assert tc.issue_task_capability({"id": "t"}, audience="w", get_task=lambda _id: None, role_grants=ROLES) is None


def test_the_hub_forwards_only_its_own_capability(monkeypatch):
    monkeypatch.setattr(tc, "issue_task_capability", lambda task, audience: {"for": task["id"], "aud": audience})
    for mode, expected in (("delegated", {"for": "t1", "aud": "http://w"}), ("hub", None), ("off", None)):
        monkeypatch.setattr(tool_loop, "get_tool_loop_config", lambda mode=mode: {"codecompass_access": mode})
        payload = {"codecompass_capability": {"forged": True}}
        forwarding._attach_codecompass_capability(payload, task={"id": "t1"}, worker_url="http://w")
        assert payload.get("codecompass_capability") == expected


def test_the_tool_loop_uses_the_received_capability(monkeypatch):
    with tc.task_capability_scope(CAP):
        assert tool_loop._task_codecompass_capability({"codecompass_access": "delegated"}, "t1") == CAP
        assert tool_loop._task_codecompass_capability({"codecompass_access": "hub"}, "t1") is None
    monkeypatch.setattr("agent.config.settings.role", "worker")
    assert tool_loop._task_codecompass_capability({}, "t1") is None  # a worker never issues one itself
