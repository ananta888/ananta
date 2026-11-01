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
    FINDING_ABSTAINED,
    FINDING_DISAGREEMENT,
    FINDING_REPORTED,
    VERDICT_TASK_ID,
    DecisionReviewVerdictProducer,
    _decision_text,
    build_review_verdict_producer,
)
from agent.services.self_development.review_workflow import QueueAcceptanceReviewer
from agent.services.vision_decision_provider import MAX_TEXT_CHARS
from agent.services.vision_decision_task_policy import DEFAULT_VISION_TASK_CATALOG

_REQUIREMENTS = {"acceptance_criteria": ["criterion A text", "criterion B text"]}


class _Stage:
    def __init__(self, *, outcome="accepted", answers=None, raises=None):
        self.outcome = outcome
        self.answers = answers or {}
        self.raises = raises
        self.seen = []

    def attempt(self, request, timeout):
        self.seen.append({"request": request, "timeout": timeout})
        if self.raises is not None:
            raise self.raises
        result = SimpleNamespace(answers=dict(self.answers))
        return SimpleNamespace(provider_id="fake", outcome=self.outcome, latency_ms=1.0), (
            result if self.outcome == "accepted" else None
        )


def _answer(value):
    return SimpleNamespace(probability=1.0 if value else 0.0)


def _producer(stage):
    return DecisionReviewVerdictProducer(stage=stage)


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


def test_questions_cover_every_criterion_plus_the_findings_check():
    stage = _Stage(
        answers={"criterion_0": _answer(True), "criterion_1": _answer(True), "findings_reported": _answer(False)}
    )
    _producer(stage).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    question = stage.seen[0]["request"]
    assert [q.key for q in question.questions] == ["criterion_0", "criterion_1", "findings_reported"]
    assert all(q.kind == "noul" for q in question.questions)
    assert "criterion A text" in question.questions[0].instructions
    assert question.purpose == "self_development_review"


def test_agreed_true_verdict_approves():
    stage = _Stage(
        answers={"criterion_0": _answer(True), "criterion_1": _answer(True), "findings_reported": _answer(False)}
    )
    result = _producer(stage).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="clean")
    assert result == AcceptanceReviewResult(approved=True, criteria_passed=(True, True), findings=())


def test_agreed_false_criterion_blocks_without_extra_finding():
    stage = _Stage(
        answers={"criterion_0": _answer(True), "criterion_1": _answer(False), "findings_reported": _answer(False)}
    )
    result = _producer(stage).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert result == AcceptanceReviewResult(approved=False, criteria_passed=(True, False), findings=())


def test_reported_findings_block_an_otherwise_clean_verdict():
    stage = _Stage(
        answers={"criterion_0": _answer(True), "criterion_1": _answer(True), "findings_reported": _answer(True)}
    )
    result = _producer(stage).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert result == AcceptanceReviewResult(approved=False, criteria_passed=(True, True), findings=(FINDING_REPORTED,))


def test_engine_disagreement_blocks_the_acceptance():
    result = _producer(_Stage(outcome="disagreement")).produce(
        requirements=_REQUIREMENTS, changed_files={}, analysis="a convincing lie"
    )
    assert result == AcceptanceReviewResult(
        approved=False, criteria_passed=(False, False), findings=(FINDING_DISAGREEMENT,)
    )


def test_low_confidence_blocks_the_acceptance():
    result = _producer(_Stage(outcome="low_confidence")).produce(
        requirements=_REQUIREMENTS, changed_files={}, analysis="..."
    )
    assert result == AcceptanceReviewResult(
        approved=False, criteria_passed=(False, False), findings=(FINDING_ABSTAINED,)
    )


def test_technical_failures_fail_closed():
    from agent.services.decision_providers.types import DecisionProviderError

    with pytest.raises(AcceptanceReviewError) as excinfo:
        _producer(_Stage(raises=DecisionProviderError("timeout", retryable=True))).produce(
            requirements=_REQUIREMENTS, changed_files={}, analysis="..."
        )
    assert excinfo.value.reason_code == "review_verdict_decision_failed"

    with pytest.raises(AcceptanceReviewError) as excinfo:
        _producer(_Stage(outcome="error")).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_decision_failed"

    stage = _Stage()
    with pytest.raises(AcceptanceReviewError) as excinfo:
        _producer(stage).produce(requirements={}, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_criteria_mismatch"
    assert stage.seen == []

    huge = {"acceptance_criteria": [f"c{i}" for i in range(32)]}
    with pytest.raises(AcceptanceReviewError) as excinfo:
        _producer(stage).produce(requirements=huge, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_schema_too_large"
    assert stage.seen == []


def test_factory_activates_an_agreement_of_two_engines(monkeypatch):
    import agent.services.self_development.review_verdict_decision as module

    settings = {
        "enabled": True,
        "providers": {
            "laya": {
                "enabled": True,
                "base_url": "http://laya:8080",
                "model": "",
                "allowed_hosts": ["laya"],
                "api_key_env": "LAYA_API_KEY",
                "timeout_seconds": 5.0,
                "max_retries": 1,
                "max_state_chars": 32000,
            },
            "local_decision": {
                "enabled": True,
                "base_url": "http://egpu-code:18150",
                "base_url_env": "",
                "timeout_seconds": 20.0,
                "max_inflight": 4,
                "require_cache_isolation": False,
                "scoped_prefix_cache": False,
            },
        },
    }
    monkeypatch.setattr(module, "_provider_settings", lambda: settings)
    producer = build_review_verdict_producer()
    assert isinstance(producer, DecisionReviewVerdictProducer)
    stage = producer._stage
    assert len(stage.providers) == 2
    assert {p.provider_id for p in stage.providers} == {"laya", "llamacpp_decision"}


def test_factory_is_inactive_without_decision_engines(monkeypatch):
    import agent.services.self_development.review_verdict_decision as module

    monkeypatch.setattr(module, "_provider_settings", lambda: None)
    assert build_review_verdict_producer() is None
    monkeypatch.setattr(module, "_provider_settings", lambda: {"enabled": False, "providers": {}})
    assert build_review_verdict_producer() is None
    monkeypatch.setattr(
        module,
        "_provider_settings",
        lambda: {"enabled": True, "providers": {"laya": {"enabled": False}, "local_decision": {"enabled": False}}},
    )
    assert build_review_verdict_producer() is None


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
    receipt = _collect(_reviewer(tmp_path, producer), tmp_path, '{"not": "a verdict at all"')
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
