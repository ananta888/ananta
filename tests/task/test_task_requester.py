"""WCRB-006: tasks record who asked for them; derived tasks inherit it; deleted roles grant nothing."""

import uuid
from types import SimpleNamespace

import pytest

from agent.services import access_role_admin_service
from agent.services.access_roles import BUILTIN_ROLES
from agent.services.task_requester import requester_fields, requester_grants, resolve_requester

pytestmark = pytest.mark.timeout(60)
ROLES = {role_id: spec["grants"] for role_id, spec in BUILTIN_ROLES.items()}


def test_a_stated_requester_is_kept_and_nothing_is_read_at_ingestion():
    assert requester_fields({"requested_by_subject": "anna"}) == {}
    assert requester_fields({}) == {}  # no request context: no requester, no database read


def test_derived_tasks_resolve_their_requester_through_their_parents():
    tasks = {"root": SimpleNamespace(id="root", requested_by_subject="anna", parent_task_id=None),
             "mid": SimpleNamespace(id="mid", requested_by_subject=None, parent_task_id="root"),
             "leaf": SimpleNamespace(id="leaf", requested_by_subject=None, parent_task_id="mid"),
             "loop": SimpleNamespace(id="loop", requested_by_subject=None, parent_task_id="loop")}
    assert resolve_requester(tasks["leaf"], tasks.get).id == "root"
    assert resolve_requester(tasks["loop"], tasks.get) is None
    assert resolve_requester(SimpleNamespace(requested_by_subject=None, parent_task_id="gone"), tasks.get) is None


def test_requester_grants_restrict_and_deleted_roles_grant_nothing():
    assert requester_grants(SimpleNamespace(requested_roles=[]), ROLES) is None  # no requester: no restriction
    viewer = requester_grants(SimpleNamespace(requested_roles=["viewer"]), ROLES)
    assert viewer.allows("ananta.tool.repo.grep", ["ananta.tool.read.v1"])[0]
    assert not viewer.allows("ananta.tool.test.run", ["ananta.tool.execution.v1"])[0]
    gone = requester_grants(SimpleNamespace(requested_roles=["deleted-role"]), ROLES)
    assert gone.roles == {"deleted-role"}
    assert not gone.allows("ananta.tool.repo.grep", ["ananta.tool.read.v1"])[0]


def test_tasks_created_by_a_user_carry_the_user_and_bound_roles(client, admin_auth_header, user_auth_header,
                                                                monkeypatch):
    from agent.repository import task_repo

    monkeypatch.setattr(access_role_admin_service, "_SERVICE", None)
    client.post("/access/bindings", headers=admin_auth_header,
                json={"subject_kind": "local_user", "subject": "testuser", "role_id": "developer"})
    parent_id = "wcrb-parent-" + uuid.uuid4().hex[:8]
    created = client.post("/tasks", json={"id": parent_id, "description": "parent"}, headers=user_auth_header)
    assert created.status_code in (200, 201), created.json
    parent = task_repo.get_by_id(parent_id)
    assert parent.requested_by_subject == "testuser" and "developer" in parent.requested_roles

    # a task derived later from the user's task resolves to the user's requester
    child_id = "wcrb-child-" + uuid.uuid4().hex[:8]
    from agent.services.task_queue_service import get_task_queue_service

    get_task_queue_service().ingest_task(task_id=child_id, status="todo", description="child",
                                         extra_fields={"parent_task_id": parent_id})
    child = task_repo.get_by_id(child_id)
    assert child.requested_by_subject is None
    assert resolve_requester(child, task_repo.get_by_id).id == parent_id
