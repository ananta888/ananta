"""The independent review runs as its own queue Task on a separate Worker.

Hub-owned phases: development dispatch admits the review (queue Task + reserved
Registry run), the review Task's own dispatch exports the sealed snapshot and
collects the Registry-bound verdict, and only a passing receipt publishes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.ports.task_dispatch import TaskDispatchResult
from agent.services.self_development.acceptance_review import AcceptanceReviewError
from agent.services.self_development.contracts import form
from agent.services.self_development.policy import DevelopmentPolicy
from agent.services.self_development.review_workflow import (
    QueueAcceptanceReviewer,
    build_review_brief,
    parse_review_verdict,
    select_reviewer_worker,
)
from agent.services.self_development.workspace import git
from tests.self_development.test_execution import execute, runtime  # noqa: F401  (fixture re-export)

AUTHOR = "http://registered-worker:5000"
REVIEWER = "http://review-worker:5000"


def enable_phased_review(service, *, review_tasks: dict | None = None, choose_worker=None) -> QueueAcceptanceReviewer:
    service.policy.value["quality_gates"] = {"require_acceptance_review": True}
    service.policy.path.write_text(json.dumps(service.policy.value))
    service.policy = DevelopmentPolicy(service.policy.path)

    def resolve(author):
        if choose_worker is not None:
            return choose_worker(author)
        if author.rstrip("/") == REVIEWER:
            raise AcceptanceReviewError("independent_acceptance_reviewer_unavailable")
        return REVIEWER

    review_tasks = review_tasks if review_tasks is not None else {}
    reviewer = QueueAcceptanceReviewer(
        queue=service.queue,
        registry=service.registry,
        checkout=service.policy.checkout,
        sealed_root=service.policy.root / "sealed",
        workspaces=service.policy.workspaces,
        tenant_id=service.policy.value["tenant_id"],
        team_id=service.policy.value["team_id"],
        policy_digest=service.policy.digest,
        choose_worker=resolve,
        task_lookup=review_tasks.get,
    )
    service.reviewer = reviewer
    return reviewer


def admit_document(service, *, criteria=("Verify behavior", "Run regression checks")):
    document = form()
    document["tasks"][0]["acceptance_criteria"] = list(criteria)
    source = service.policy.checkout / "todos/active/todo.review.json"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(document))
    git(service.policy.checkout, "add", "--", "todos/active/todo.review.json")
    git(service.policy.checkout, "commit", "-m", "test(autopilot): add synthetic review task")
    service.import_tasks()
    service.start()


def completed_review_task(review_id, *, command="python -m pytest", url=REVIEWER, exit_code=0, status="completed"):
    return SimpleNamespace(
        status=status, last_exit_code=exit_code, assigned_agent_url=url, last_proposal={"command": command}
    )


def run_review_worker(review_task, *, approved=True, passed=(True, True), findings=(), answer=None):
    workspace = Path(review_task.worker_execution_context["workspace"]["output_dir"])
    assert not (workspace / ".git").exists()
    assert (workspace / "tests/self_development/test_example.py").is_file()
    assert (workspace / ".ananta" / "review.json").is_file()
    (workspace / ".ananta").mkdir(exist_ok=True)
    verdict = (
        answer
        if answer is not None
        else json.dumps({"approved": approved, "criteria_passed": list(passed), "findings": list(findings)})
    )
    (workspace / ".ananta" / "last-final-answer.json").write_text(
        json.dumps({"outcome": "final_answer", "answer": verdict}), encoding="utf-8"
    )
    return TaskDispatchResult(review_task.id, dispatched=True, completed=True)


def dispatch_review(service, item, review_tasks, *, worker_url=REVIEWER, **kwargs):
    review_id = item["review_task_id"]
    task_kwargs = {k: v for k, v in kwargs.items() if k in {"command", "url", "exit_code", "status"}}
    review_tasks[review_id] = completed_review_task(review_id, **task_kwargs)
    review_task = SimpleNamespace(id=review_id, status="todo")
    execution = {k: v for k, v in kwargs.items() if k in {"approved", "passed", "findings", "answer"}}
    return service.dispatch(
        review_task, SimpleNamespace(url=worker_url), lambda: run_review_worker(review_task, **execution)
    )


def test_phased_review_dispatches_separate_task_then_publishes(runtime):
    service, ingested, published = runtime
    review_tasks: dict = {}
    enable_phased_review(service, review_tasks=review_tasks)
    admit_document(service)

    dev_result = execute(service)

    assert dev_result.failure_type == "development_awaiting_acceptance_review"
    item = service.status()["items"][0]
    assert item["status"] == "pending_review"
    assert item["reason"] == "independent_acceptance_review_pending"
    assert published == []
    review_calls = [c for c in ingested if c["extra_fields"].get("task_kind") == "gate_review"]
    assert len(review_calls) == 1
    call = review_calls[0]
    assert call["status"] == "todo"
    assert call["extra_fields"]["assigned_agent_url"] == REVIEWER
    assert {"review", "analysis"} <= set(call["extra_fields"]["required_capabilities"])
    assert call["event_details"]["review_of_task"] == item["task_id"]
    reservation = item["review_reservation"]
    assert reservation["reviewer_worker_url"] == REVIEWER
    assert reservation["run_id"].startswith("RUN_")
    assert service.manages(reservation["review_task_id"])

    review_result = dispatch_review(service, item, review_tasks)

    assert review_result.completed, service.status()["items"][0]["reason"]
    assert published == [item["task_id"]]
    final = service.status()["items"][0]
    assert final["status"] == "published"
    assert final["acceptance_review"]["worker_url"] == REVIEWER
    assert final["acceptance_review"]["result"]["criteria_passed"] == [True, True]
    state = service.journal.read()
    service._verify(state, state["items"][final["key"]])


def test_review_worker_sees_sealed_snapshot_and_brief(runtime):
    service, ingested, _ = runtime
    review_tasks: dict = {}
    enable_phased_review(service, review_tasks=review_tasks)
    admit_document(service)
    execute(service)
    item = service.status()["items"][0]
    review_id = item["review_task_id"]
    review_tasks[review_id] = completed_review_task(review_id)
    seen = {}

    def review_execute():
        seen["context"] = review_task.worker_execution_context
        return run_review_worker(review_task)

    review_task = SimpleNamespace(id=review_id, status="todo")
    service.dispatch(review_task, SimpleNamespace(url=REVIEWER), review_execute)

    context = seen["context"]
    assert context["hub_evidence_assignment"]["run_id"].startswith("RUN_")
    workspace = Path(context["workspace"]["output_dir"])
    brief = review_task.description
    assert "Independent acceptance review" in brief
    assert "Verify behavior" in brief
    assert "tests/self_development/test_example.py" in brief
    assert "python -m pytest" in brief
    manifest = json.loads((workspace / ".ananta" / "review.json").read_text())
    assert manifest["author_task_id"] == item["task_id"]
    assert manifest["changed_files"] == {
        "tests/self_development/test_example.py": item["acceptance_review_request"]["changed_files"][0][1]
    }
    assert "materialization-manifest.json" not in {p.name for p in (workspace / ".ananta").iterdir()}


def test_reviewer_selection_refuses_author_and_requires_online():
    workers = [SimpleNamespace(url=AUTHOR + "/", worker_roles=["reviewer"], capabilities=["review"])]
    with pytest.raises(AcceptanceReviewError, match="reviewer_unavailable"):
        select_reviewer_worker(AUTHOR, workers=workers)
    eligible = [
        SimpleNamespace(url="http://generic:5000", worker_roles=["coder"], capabilities=[]),
        SimpleNamespace(url="http://c:5000", worker_roles=["reviewer"], capabilities=["review"]),
    ]
    assert select_reviewer_worker(AUTHOR, workers=eligible) == "http://c:5000"


def test_missing_eligible_reviewer_fails_development_bounded(runtime):
    service, ingested, published = runtime

    def no_reviewer(author):
        raise AcceptanceReviewError("independent_acceptance_reviewer_unavailable")

    enable_phased_review(service, choose_worker=no_reviewer)
    admit_document(service)

    result = execute(service)

    assert result.failed
    assert published == []
    item = service.status()["items"][0]
    assert item["status"] == "failed"
    assert item["reason"] == "independent_acceptance_reviewer_unavailable"
    assert not [c for c in ingested if c["extra_fields"].get("task_kind") == "gate_review"]


@pytest.mark.parametrize(
    "scenario",
    [
        ("self_review", {"worker_url": AUTHOR}, "review_worker_binding_mismatch"),
        ("negative", {"approved": False}, "acceptance_criteria_not_satisfied"),
        ("findings", {"findings": ("Naming unclear",)}, "acceptance_criteria_not_satisfied"),
        ("unparseable", {"answer": "looks good to me"}, "worker_review_verdict_unparseable"),
        ("failed_execution", {"exit_code": 1}, "worker_review_verification_failed"),
        ("wrong_command", {"command": "make lint"}, "worker_review_verification_failed"),
    ],
)
def test_failed_review_never_publishes(runtime, scenario):
    label, worker_kwargs, expected = scenario
    service, _, published = runtime
    review_tasks: dict = {}
    enable_phased_review(service, review_tasks=review_tasks)
    admit_document(service)
    assert execute(service).failed
    item = service.status()["items"][0]
    assert item["status"] == "pending_review"

    result = dispatch_review(service, item, review_tasks, **worker_kwargs)

    final = service.status()["items"][0]
    assert result.failed
    assert result.failure_type == "acceptance_review_failed"
    assert published == []
    assert final["status"] == "failed"
    assert expected in final["reason"], final["reason"]


def test_operator_stop_before_review_dispatch_fails_bounded(runtime):
    service, _, published = runtime
    review_tasks: dict = {}
    enable_phased_review(service, review_tasks=review_tasks)
    admit_document(service)
    execute(service)
    item = service.status()["items"][0]
    service.stop()

    result = dispatch_review(service, item, review_tasks)

    final = service.status()["items"][0]
    assert result.failed
    assert published == []
    assert "development_session_stopped_or_budget_exhausted" in final["reason"], final["reason"]


def test_stale_review_redispatch_is_rejected(runtime):
    service, _, published = runtime
    review_tasks: dict = {}
    enable_phased_review(service, review_tasks=review_tasks)
    admit_document(service)
    execute(service)
    item = service.status()["items"][0]
    assert dispatch_review(service, item, review_tasks).completed
    assert published == [item["task_id"]]

    rejected = dispatch_review(service, item, review_tasks)

    final = service.status()["items"][0]
    assert rejected.failed
    assert "stale_review_attempt" in final["reason"], final["reason"]
    assert published == [item["task_id"]]


def test_retry_supersedes_pending_review_task(runtime, monkeypatch):
    service, ingested, published = runtime
    review_tasks: dict = {}
    enable_phased_review(service, review_tasks=review_tasks)
    service.policy.value["max_attempts"] = 2
    service.policy.path.write_text(json.dumps(service.policy.value))
    service.policy = DevelopmentPolicy(service.policy.path)
    admit_document(service)
    execute(service)
    item = service.status()["items"][0]
    assert dispatch_review(service, item, review_tasks, approved=False).failed
    cancellations = []
    monkeypatch.setattr(
        "agent.services.task_runtime_service.update_local_task_status",
        lambda tid, status, **kw: cancellations.append((tid, status)),
    )
    old_review_id = item["review_task_id"]
    old_task_id = item["task_id"]

    assert service.retry()["retried"] == 1

    retry_item = service.status()["items"][0]
    assert retry_item["status"] == "ready"
    assert retry_item["task_id"] != old_task_id
    for key in (
        "review_task_id",
        "review_reservation",
        "review_assignment",
        "review_verification_command",
        "acceptance_review",
        "acceptance_review_request",
    ):
        assert key not in retry_item
    assert cancellations == [(old_review_id, "cancelled")]


def test_verdict_parser_bounds_and_order():
    verdict = parse_review_verdict(
        'Preamble {not json\n{"approved": true, "criteria_passed": [true, false], "findings": []}', criteria_count=2
    )
    assert verdict.approved is True
    assert verdict.criteria_passed == (True, False)
    with pytest.raises(ValueError, match="unparseable"):
        parse_review_verdict("no json here", criteria_count=2)
    with pytest.raises(ValueError, match="criteria_mismatch"):
        parse_review_verdict('{"approved": true, "criteria_passed": [true], "findings": []}', criteria_count=2)
    with pytest.raises(ValueError, match="unparseable"):
        parse_review_verdict('{"approved": "yes", "criteria_passed": [true], "findings": []}', criteria_count=1)


def test_review_brief_is_bounded_and_declares_contract():
    brief = build_review_brief(
        todo_id="MY-001",
        requirements={"acceptance_criteria": ["A", "B"], "todo_id": "MY-001"},
        changed_files={"a.py": "hash"},
        verification_command="python -m pytest",
    )
    assert '{"approved": <true|false>' in brief
    assert "A" in brief and "B" in brief
    with pytest.raises(ValueError, match="brief_too_large"):
        build_review_brief(
            todo_id="MY-001",
            requirements={"acceptance_criteria": ["x" * 20000]},
            changed_files={},
            verification_command="python -m pytest",
        )
