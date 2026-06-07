from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

from agent import repository
from agent.services.blueprint_planning_adapter import (
    BlueprintPlanningAdapter,
    _stable_topo_order,
)
from tests_support import admin_login_token as _login_admin


def _ensure_seed_blueprints(client) -> None:
    token = _login_admin(client)
    response = client.get("/teams/blueprints", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200


def test_blueprint_planning_adapter_resolves_seed_blueprint_subtasks(app, client) -> None:
    _ensure_seed_blueprints(client)
    adapter = BlueprintPlanningAdapter()
    with app.app_context():
        resolution = adapter.resolve("TDD")

    assert resolution is not None
    assert resolution.degraded is False
    assert resolution.blueprint_name == "TDD"
    assert resolution.subtasks
    assert resolution.artifact_refs
    first = resolution.subtasks[0]
    assert first["blueprint_name"] == "TDD"
    # WFG-006: when the blueprint has a workflow block
    # (which all standard blueprints do after WFG-018),
    # the subtask key is ``blueprint_workflow_step_id``.
    # The legacy ``blueprint_artifact_id`` key is only
    # present on the legacy subtask path. We accept
    # either, depending on which path the resolver took.
    assert (
        first.get("blueprint_workflow_step_id")
        or first.get("blueprint_artifact_id")
    ), first
    assert isinstance(first.get("blueprint_role_hints"), list)
    assert isinstance(first.get("blueprint_role_template_hints"), list)


def test_blueprint_planning_adapter_resolves_fuzzy_goal_text(app, client) -> None:
    _ensure_seed_blueprints(client)
    adapter = BlueprintPlanningAdapter()
    with app.app_context():
        subtasks = adapter.resolve_subtasks("Bitte TDD blueprint fuer login bugfix ausfuehren")

    assert subtasks is not None
    assert len(subtasks) >= 1


def test_blueprint_planning_adapter_degrades_when_repo_unavailable(app, monkeypatch) -> None:
    adapter = BlueprintPlanningAdapter()

    def _raise_db_error():  # noqa: ANN202
        raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(repository.team_blueprint_repo, "get_all", _raise_db_error)
    with app.app_context():
        resolution = adapter.resolve("TDD")

    assert resolution is not None
    assert resolution.degraded is True
    assert resolution.subtasks == []
    assert "blueprint_repo_unavailable" in str(resolution.degraded_reason or "")


# ── WFG-006: workflow-first subtask builder tests ──────────────────────


def _step(step_id, role_name, *, depends_on=(), task_kind="coding", gate=False, sort_order=0, checks=None, failure_policy=None):
    return SimpleNamespace(
        id=f"row-{step_id}",
        step_id=step_id,
        role_name=role_name,
        task_kind=task_kind,
        title=f"Step {step_id}",
        description="",
        sort_order=sort_order,
        depends_on=list(depends_on),
        gate=gate,
        checks=checks or {},
        failure_policy=failure_policy,
        required_capabilities=[],
        produces=[],
        consumes=[],
    )


def test_adapter_uses_workflow_steps_when_present(app, client, monkeypatch) -> None:
    """WFG-006: a blueprint with workflow steps uses the workflow builder
    and produces subtasks in topological order, ignoring task artifacts."""
    _ensure_seed_blueprints(client)
    blueprint = SimpleNamespace(id="bp-1", name="TDD", description="")

    steps = [
        _step("a", "Planner", sort_order=0),
        _step("b", "Developer", depends_on=["a"], sort_order=1),
        _step("c", "Reviewer", depends_on=["b"], gate=True,
              checks={"min_artifacts": ["x"]}, failure_policy="block", sort_order=2),
    ]

    def fake_get_all():
        return [blueprint]

    class _RepoStub:
        def get_by_blueprint(self, blueprint_id):
            if blueprint_id == "bp-1":
                return steps
            return []

    def _empty_roles(_id):
        return []

    repos = SimpleNamespace(
        team_blueprint_repo=SimpleNamespace(get_all=fake_get_all),
        blueprint_artifact_repo=_RepoStub(),
        blueprint_role_repo=SimpleNamespace(get_by_blueprint=_empty_roles),
        blueprint_workflow_step_repo=_RepoStub(),
        template_repo=SimpleNamespace(get_by_id=lambda _id: None),
    )

    from agent.services import blueprint_planning_adapter as bpa
    monkeypatch.setattr(bpa, "get_repository_registry", lambda: repos)

    with app.app_context():
        resolution = BlueprintPlanningAdapter().resolve("TDD")

    assert resolution is not None
    assert resolution.degraded is False
    assert [s["blueprint_workflow_step_id_label"] for s in resolution.subtasks] == ["a", "b", "c"]
    assert resolution.artifact_refs == [
        "blueprint_workflow_step:row-a",
        "blueprint_workflow_step:row-b",
        "blueprint_workflow_step:row-c",
    ]
    # Gate info is propagated to the planner
    assert resolution.subtasks[2]["gate"] is True
    assert resolution.subtasks[2]["checks"] == {"min_artifacts": ["x"]}
    assert resolution.subtasks[2]["failure_policy"] == "block"


def test_adapter_falls_back_to_artifacts_when_no_workflow_steps(app, client, monkeypatch) -> None:
    """WFG-006 / WFG-021: a blueprint without workflow steps falls back
    to the artifact-based subtask builder (backward compatibility)."""
    _ensure_seed_blueprints(client)
    blueprint = SimpleNamespace(id="bp-2", name="TDD", description="")
    artifact = SimpleNamespace(
        id="art-1", kind="task", title="legacy task", description="d", sort_order=0, payload={}
    )

    def fake_get_all():
        return [blueprint]

    class _Empty:
        def get_by_blueprint(self, blueprint_id):
            return []

    repos = SimpleNamespace(
        team_blueprint_repo=SimpleNamespace(get_all=fake_get_all),
        blueprint_artifact_repo=SimpleNamespace(get_by_blueprint=lambda _id: [artifact]),
        blueprint_role_repo=_Empty(),
        blueprint_workflow_step_repo=_Empty(),
        template_repo=SimpleNamespace(get_by_id=lambda _id: None),
    )

    from agent.services import blueprint_planning_adapter as bpa
    monkeypatch.setattr(bpa, "get_repository_registry", lambda: repos)

    with app.app_context():
        resolution = BlueprintPlanningAdapter().resolve("TDD")

    assert resolution is not None
    assert resolution.subtasks and resolution.subtasks[0]["blueprint_artifact_id"] == "art-1"
    assert resolution.artifact_refs == ["blueprint_artifact:art-1"]


def test_adapter_swallows_workflow_repo_sqlalchemy_error(app, client, monkeypatch) -> None:
    """WFG-006: a SQLAlchemyError on the workflow step repo degrades
    gracefully to the legacy artifact path (WFG-021 compatibility)."""
    _ensure_seed_blueprints(client)
    blueprint = SimpleNamespace(id="bp-3", name="TDD", description="")
    artifact = SimpleNamespace(
        id="art-2", kind="task", title="fallback task", description="d", sort_order=0, payload={}
    )

    def fake_get_all():
        return [blueprint]

    class _Boom:
        def get_by_blueprint(self, blueprint_id):
            raise SQLAlchemyError("workflow repo down")

    class _Good:
        def get_by_blueprint(self, blueprint_id):
            return [artifact]

    repos = SimpleNamespace(
        team_blueprint_repo=SimpleNamespace(get_all=fake_get_all),
        blueprint_artifact_repo=_Good(),
        blueprint_role_repo=SimpleNamespace(get_by_blueprint=lambda _id: []),
        blueprint_workflow_step_repo=_Boom(),
        template_repo=SimpleNamespace(get_by_id=lambda _id: None),
    )

    from agent.services import blueprint_planning_adapter as bpa
    monkeypatch.setattr(bpa, "get_repository_registry", lambda: repos)

    with app.app_context():
        resolution = BlueprintPlanningAdapter().resolve("TDD")

    assert resolution is not None
    assert resolution.degraded is False  # degraded only on blueprint_repo, not workflow
    assert resolution.subtasks[0]["blueprint_artifact_id"] == "art-2"


def test_stable_topo_order_empty() -> None:
    assert _stable_topo_order([]) == []


def test_stable_topo_order_respects_depends_on() -> None:
    steps = [
        _step("b", "Dev", depends_on=["a"], sort_order=0),
        _step("a", "Plan", sort_order=1),
    ]
    ordered = _stable_topo_order(steps)
    assert [s.step_id for s in ordered] == ["a", "b"]


def test_stable_topo_order_ties_broken_by_step_id() -> None:
    """Two roots — alpha-sorted by step_id for determinism."""
    steps = [
        _step("zeta", "Z", sort_order=0),
        _step("alpha", "A", sort_order=1),
    ]
    ordered = _stable_topo_order(steps)
    assert [s.step_id for s in ordered] == ["alpha", "zeta"]


def test_stable_topo_order_skips_unknown_dep_defensively() -> None:
    """If a step references a dep that is not in the input, it is
    silently skipped (the catalog normalizer is the authoritative
    gate; we don't fail at materialization time)."""
    steps = [
        _step("a", "Plan", sort_order=0),
        _step("b", "Dev", depends_on=["ghost"], sort_order=1),
    ]
    ordered = _stable_topo_order(steps)
    assert [s.step_id for s in ordered] == ["a", "b"]
