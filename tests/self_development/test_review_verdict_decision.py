from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent.services.self_development.acceptance_review import (
    AcceptanceReviewError,
    AcceptanceReviewRequest,
    AcceptanceReviewResult,
)
from agent.services.self_development.review_verdict_decision import (
    FINDING_REPORTED,
    FINDING_UNCONFIRMED,
    VERDICT_TASK_ID,
    DecisionReviewVerdictProducer,
    _decision_text,
    build_review_verdict_producer,
)
from agent.services.self_development.review_workflow import QueueAcceptanceReviewer
from agent.services.vision_decision_provider import MAX_TEXT_CHARS
from agent.services.vision_decision_task_policy import DEFAULT_VISION_TASK_CATALOG

_REQUIREMENTS = {"acceptance_criteria": ["criterion A text", "criterion B text"]}
_MISSING = object()


def _field(*, accepted=True, value=True):
    return SimpleNamespace(accepted=accepted, value=value)


def _outcome(fields, *, ok=True):
    return SimpleNamespace(ok=ok, results=[SimpleNamespace(fields=fields)])


class _FakeProvider:
    def __init__(self, outcome=None, *, schemas=()):
        self.config = SimpleNamespace(schemas=tuple(schemas))
        self.outcome = outcome
        self.calls = []

    def decide(self, schema, contexts, **kwargs):
        self.calls.append({"schema": schema, "contexts": contexts, "kwargs": kwargs})
        return self.outcome


def _producer(outcome=None, *, schemas=(), loader_result=_MISSING, loader_raises=False):
    provider = _FakeProvider(outcome, schemas=schemas)

    def loader():
        if loader_raises:
            raise RuntimeError("boom")
        if loader_result is not _MISSING:
            return loader_result
        return SimpleNamespace(provider=provider)

    return DecisionReviewVerdictProducer(runtime_loader=loader), provider


def test_review_verdict_task_is_registered_and_informational():
    task = DEFAULT_VISION_TASK_CATALOG[VERDICT_TASK_ID]
    assert task.status == "experimental"
    assert set(task.fields) == {"approved"}
    assert task.fields["approved"].actions == {}
    assert task.schema.schema_id == VERDICT_TASK_ID


def test_decision_text_fits_provider_limit_and_keeps_the_analysis():
    text = _decision_text(
        requirements={"acceptance_criteria": ["x" * 200] * 20},
        changed_files={f"path/to/file_{index}.py": "d" * 64 for index in range(50)},
        analysis="a" * 5_000,
    )
    assert len(text) <= MAX_TEXT_CHARS
    assert "Reviewer analysis:" in text


def test_producer_assembles_approval_from_confirmed_criteria():
    producer, provider = _producer(
        _outcome(
            {
                "criterion_0": _field(value=True),
                "criterion_1": _field(value=True),
                "no_findings": _field(value=True),
            }
        )
    )
    result = producer.produce(requirements=_REQUIREMENTS, changed_files={}, analysis="clean")
    assert result == AcceptanceReviewResult(approved=True, criteria_passed=(True, True), findings=())
    call = provider.calls[0]
    assert call["kwargs"] == {"require_media": False}
    assert set(call["schema"].fields and [f.name for f in call["schema"].fields]) == {
        "criterion_0",
        "criterion_1",
        "no_findings",
    }


def test_producer_never_approves_without_every_criterion():
    producer, _ = _producer(
        _outcome(
            {
                "criterion_0": _field(value=True),
                "criterion_1": _field(value=False),
                "no_findings": _field(value=True),
            }
        )
    )
    result = producer.produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert result == AcceptanceReviewResult(approved=False, criteria_passed=(True, False), findings=())


def test_producer_treats_unconfirmed_fields_as_findings():
    producer, _ = _producer(
        _outcome(
            {
                "criterion_0": _field(value=True),
                "criterion_1": _field(accepted=False, value=None),
                "no_findings": _field(accepted=False, value=None),
            }
        )
    )
    result = producer.produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert result.approved is False
    assert result.criteria_passed == (True, False)
    assert result.findings == ("criterion_unconfirmed:1", FINDING_UNCONFIRMED)


def test_producer_records_reported_findings():
    producer, _ = _producer(
        _outcome({"criterion_0": _field(), "criterion_1": _field(), "no_findings": _field(value=False)})
    )
    result = producer.produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert result == AcceptanceReviewResult(
        approved=False, criteria_passed=(True, True), findings=(FINDING_REPORTED,)
    )


def test_producer_fails_closed_on_technical_errors():
    producer, _ = _producer(_outcome({}, ok=False))
    with pytest.raises(AcceptanceReviewError) as excinfo:
        producer.produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_decision_failed"

    producer, _ = _producer(_outcome({}), loader_result=None)
    with pytest.raises(AcceptanceReviewError) as excinfo:
        producer.produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_decision_failed"

    producer, _ = _producer(_outcome({}), loader_raises=True)
    with pytest.raises(AcceptanceReviewError) as excinfo:
        producer.produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_decision_failed"


def test_producer_requires_bounded_criteria():
    producer, provider = _producer(_outcome({}))
    with pytest.raises(AcceptanceReviewError) as excinfo:
        producer.produce(requirements={}, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_criteria_mismatch"
    assert provider.calls == []

    huge = {"acceptance_criteria": [f"c{i}" for i in range(32)]}
    with pytest.raises(AcceptanceReviewError) as excinfo:
        producer.produce(requirements=huge, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_schema_too_large"
    assert provider.calls == []


def test_factory_producer_only_when_task_enabled(monkeypatch):
    module = __import__(
        "agent.services.self_development.review_verdict_decision", fromlist=["_default_runtime_loader"]
    )
    monkeypatch.setattr(module, "_default_runtime_loader", lambda: None)
    assert build_review_verdict_producer() is None
    monkeypatch.setattr(
        module, "_default_runtime_loader", lambda: SimpleNamespace(provider=_FakeProvider(None, schemas=("shapes-v1",)))
    )
    assert build_review_verdict_producer() is None
    monkeypatch.setattr(module, "_default_runtime_loader", lambda: SimpleNamespace(provider=_FakeProvider(None)))
    assert build_review_verdict_producer() is not None


class _RecordingProducer:
    def __init__(self, result: AcceptanceReviewResult) -> None:
        self.result = result
        self.seen: list[dict] = []

    def produce(self, *, requirements, changed_files, analysis):
        self.seen.append({"requirements": requirements, "changed_files": changed_files, "analysis": analysis})
        return self.result


def _collect(reviewer, tmp_path, answer: str):
    workspace = tmp_path / "ws"
    (workspace / ".ananta").mkdir(parents=True)
    (workspace / ".ananta" / "last-final-answer.json").write_text(
        json.dumps({"outcome": "final_answer", "answer": answer}), encoding="utf-8"
    )
    request = AcceptanceReviewRequest(
        task_id="task-1",
        repository_revision="rev-1",
        author_worker_url="http://author",
        requirements_json=json.dumps({"acceptance_criteria": ["c1"]}),
        changed_files=(("a.py", "abc"),),
    )
    reservation = {
        "review_task_id": "review-1",
        "reviewer_worker_url": "http://reviewer",
        "run_id": "run-1",
        "assignment_id": "assign-1",
        "dispatch_lease_id": "lease-1",
    }
    return reviewer.collect_review(request, reservation, workspace=str(workspace), verification_command="pytest -q")


def _reviewer(tmp_path, producer):
    return QueueAcceptanceReviewer(
        queue=None,
        registry=SimpleNamespace(record_result=lambda **kwargs: "run-identity"),
        checkout=None,
        sealed_root=tmp_path / "sealed",
        workspaces=tmp_path,
        tenant_id="tenant",
        team_id="team",
        policy_digest="digest",
        choose_worker=lambda _author: "http://reviewer",
        task_lookup=lambda _task_id: SimpleNamespace(
            status="completed",
            last_exit_code=0,
            assigned_agent_url="http://reviewer",
            last_proposal={"command": "pytest -q"},
        ),
        verdict_producer=producer,
    )


def test_collect_review_uses_decision_produced_verdict(tmp_path):
    produced = AcceptanceReviewResult(approved=True, criteria_passed=(True,), findings=())
    producer = _RecordingProducer(produced)
    receipt = _collect(
        _reviewer(tmp_path, producer), tmp_path, '{"not": "a verdict at all"'
    )
    assert receipt.result is produced
    assert producer.seen and producer.seen[0]["analysis"]


def test_collect_review_without_producer_parses_worker_json(tmp_path):
    receipt = _collect(
        _reviewer(tmp_path, None), tmp_path, '{"approved": true, "criteria_passed": [true], "findings": []}'
    )
    assert receipt.result.approved is True


def test_collect_review_without_producer_keeps_strict_parse(tmp_path):
    with pytest.raises(Exception) as excinfo:
        _collect(_reviewer(tmp_path, None), tmp_path, "no json here")
    assert getattr(excinfo.value, "reason_code", "") == "worker_review_verdict_unparseable"


def test_analysis_brief_omits_verdict_json_contract():
    from agent.services.self_development.review_workflow import VERDICT_CONTRACT, build_review_brief

    brief = build_review_brief(
        todo_id="t",
        requirements={"acceptance_criteria": ["c1"]},
        changed_files={"a.py": "abc"},
        verification_command="pytest -q",
        attempt=1,
        verdict_from_decision=True,
    )
    assert VERDICT_CONTRACT not in brief
    assert "ATTEMPT" not in brief
    assert "your analysis" in brief
    assert "Run exactly: pytest -q" in brief


def test_analysis_manifest_makes_prose_answers_persistable(tmp_path):
    from agent.services.self_development.final_answer_artifact import (
        ANALYSIS_CONTRACT,
        review_answer_present,
        write_final_answer,
    )

    workspace = tmp_path / "ws"
    (workspace / ".ananta").mkdir(parents=True)
    (workspace / ".ananta" / "review.json").write_text(
        json.dumps({"output_contract": ANALYSIS_CONTRACT}), encoding="utf-8"
    )
    assert write_final_answer(workspace, task_id="t1", answer="The change adds tests. pytest passes.")
    assert review_answer_present(workspace)

    legacy = tmp_path / "legacy"
    (legacy / ".ananta").mkdir(parents=True)
    (legacy / ".ananta" / "review.json").write_text(
        json.dumps({"output_contract": "json verdict"}), encoding="utf-8"
    )
    assert not write_final_answer(legacy, task_id="t2", answer="The change adds tests.")
    assert not review_answer_present(legacy)
