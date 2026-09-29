"""Every derived task lands where the task that produced it lives.

Derivation happens in seven places and all of them ingest, so the inheritance
sits at ingestion rather than at each of them.  What is pinned here is that
the boundary actually applies it, that a caller stating its own scope is left
alone, and that a parent the repository cannot produce costs the new task its
scope and nothing else.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.services import task_queue_service as module


class _Parent:
    def __init__(self, **values: Any) -> None:
        self.tenant_id = values.get("tenant_id")
        self.project_id = values.get("project_id")
        self.organization_id = values.get("organization_id")
        self.unit_id = values.get("unit_id")
        self.team_id = values.get("team_id")
        self.role_slot_id = values.get("role_slot_id")


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Captures what ingestion would have written, without a database."""

    calls: list[dict[str, Any]] = []

    def _capture(task_id: str, status: str, **values: Any) -> None:
        calls.append({"task_id": task_id, "status": status, **values})

    monkeypatch.setattr(module, "update_local_task_status", _capture)
    return calls


def _with_parent(monkeypatch: pytest.MonkeyPatch, parent: Any) -> None:
    class _Repo:
        @staticmethod
        def get_by_id(identifier: str) -> Any:
            return parent

    monkeypatch.setattr(module, "task_repo", _Repo)


def _ingest(**kwargs: Any) -> None:
    module.TaskQueueService().ingest_task(task_id="t-new", status="todo", **kwargs)


def _organized_parent() -> _Parent:
    return _Parent(
        tenant_id="t-1",
        project_id="p-1",
        organization_id="org-1",
        unit_id="unit-1",
        team_id="team-1",
        role_slot_id="slot-1",
    )


def test_a_derived_task_is_written_into_its_parents_organisation(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    _with_parent(monkeypatch, _organized_parent())

    _ingest(extra_fields={"parent_task_id": "t-parent"}, team_id="team-1")

    assert written[0]["organization_id"] == "org-1"
    assert written[0]["unit_id"] == "unit-1"
    assert written[0]["role_slot_id"] == "slot-1"
    assert written[0]["team_id"] == "team-1"
    assert written[0]["tenant_id"] == "t-1"
    assert written[0]["project_id"] == "p-1"


def test_a_task_with_no_parent_is_written_exactly_as_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    _with_parent(monkeypatch, None)

    _ingest(team_id="team-9")

    assert written[0]["team_id"] == "team-9"
    for field in ("organization_id", "unit_id", "role_slot_id"):
        assert field not in written[0]


def test_a_caller_that_states_its_own_organisation_is_not_overruled(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    _with_parent(monkeypatch, _organized_parent())

    _ingest(extra_fields={"parent_task_id": "t-parent", "organization_id": "org-other"})

    assert written[0]["organization_id"] == "org-other"
    assert "unit_id" not in written[0]


def test_a_parent_that_cannot_be_read_costs_the_scope_and_not_the_task(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    class _BrokenRepo:
        @staticmethod
        def get_by_id(identifier: str) -> Any:
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(module, "task_repo", _BrokenRepo)

    _ingest(extra_fields={"parent_task_id": "t-parent"}, title="Etwas")

    assert written[0]["task_id"] == "t-new"
    assert "organization_id" not in written[0]


def test_the_parent_is_not_even_looked_up_when_a_scope_was_stated(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    looked_up: list[str] = []

    class _Repo:
        @staticmethod
        def get_by_id(identifier: str) -> Any:
            looked_up.append(identifier)
            return _organized_parent()

    monkeypatch.setattr(module, "task_repo", _Repo)

    _ingest(extra_fields={"parent_task_id": "t-parent", "project_id": "p-9"})

    assert looked_up == []
    assert written[0]["project_id"] == "p-9"


def test_a_task_naming_only_its_source_still_inherits(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    _with_parent(monkeypatch, _organized_parent())

    _ingest(extra_fields={"source_task_id": "t-source"})

    assert written[0]["organization_id"] == "org-1"
    assert written[0]["team_id"] == "team-1"


def test_a_team_named_against_the_parents_organisation_keeps_the_team_only(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    """A team that was never linked to that organisation is not written as if it were."""

    _with_parent(monkeypatch, _organized_parent())

    _ingest(extra_fields={"parent_task_id": "t-parent"}, team_id="team-elsewhere")

    assert written[0]["team_id"] == "team-elsewhere"
    assert "organization_id" not in written[0]
    assert written[0]["project_id"] == "p-1"


def test_ingestion_still_writes_everything_it_always_wrote(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    _with_parent(monkeypatch, None)

    _ingest(
        title="Ein Titel",
        description="Beschreibung",
        priority="high",
        created_by="mara",
        tags=["a"],
        extra_fields={"derivation_reason": "manual_followup"},
    )

    call = written[0]
    assert call["title"] == "Ein Titel"
    assert call["description"] == "Beschreibung"
    assert call["priority"] == "high"
    assert call["event_actor"] == "mara"
    assert call["tags"] == ["a"]
    assert call["derivation_reason"] == "manual_followup"


class _Goal:
    def __init__(self, **values: Any) -> None:
        self.tenant_id = values.get("tenant_id")
        self.project_id = values.get("project_id")
        self.organization_id = values.get("organization_id")
        self.unit_id = values.get("unit_id")
        self.team_id = values.get("team_id")


def _with_goal(monkeypatch: pytest.MonkeyPatch, goal: Any) -> None:
    class _Repo:
        @staticmethod
        def get_by_id(identifier: str) -> Any:
            return goal

    monkeypatch.setattr(module, "goal_repo", _Repo)


def test_the_first_task_of_a_goal_takes_the_goals_organisation(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    """Without this the organisation would be filled in from the second task on."""

    _with_parent(monkeypatch, None)
    _with_goal(monkeypatch, _Goal(tenant_id="t-1", project_id="p-1", organization_id="org-1", unit_id="unit-1"))

    _ingest(extra_fields={"goal_id": "g-1"})

    assert written[0]["organization_id"] == "org-1"
    assert written[0]["unit_id"] == "unit-1"
    assert written[0]["tenant_id"] == "t-1"


def test_a_parent_outranks_the_goal_they_share(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    """A task sits where the thing that produced it sits, one step at a time."""

    _with_parent(monkeypatch, _organized_parent())
    _with_goal(monkeypatch, _Goal(tenant_id="t-9", project_id="p-9", organization_id="org-9"))

    _ingest(extra_fields={"parent_task_id": "t-parent", "goal_id": "g-1"})

    assert written[0]["organization_id"] == "org-1"


def test_a_goal_is_consulted_when_the_named_parent_no_longer_exists(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    _with_parent(monkeypatch, None)
    _with_goal(monkeypatch, _Goal(tenant_id="t-1", project_id="p-1", organization_id="org-1"))

    _ingest(extra_fields={"parent_task_id": "gone", "goal_id": "g-1"})

    assert written[0]["organization_id"] == "org-1"


def test_a_goal_outside_an_organisation_still_passes_on_its_project(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    _with_parent(monkeypatch, None)
    _with_goal(monkeypatch, _Goal(tenant_id="t-1", project_id="p-1"))

    _ingest(extra_fields={"goal_id": "g-1"})

    assert written[0]["project_id"] == "p-1"
    assert "organization_id" not in written[0]


def test_a_goal_that_cannot_be_read_costs_the_scope_and_not_the_task(
    monkeypatch: pytest.MonkeyPatch,
    written: list[dict[str, Any]],
) -> None:
    class _BrokenRepo:
        @staticmethod
        def get_by_id(identifier: str) -> Any:
            raise RuntimeError("database unavailable")

    _with_parent(monkeypatch, None)
    monkeypatch.setattr(module, "goal_repo", _BrokenRepo)

    _ingest(extra_fields={"goal_id": "g-1"})

    assert written[0]["task_id"] == "t-new"
    assert "organization_id" not in written[0]
