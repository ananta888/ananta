from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.self_development.acceptance_review import (
    AcceptanceReviewError,
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
from agent.services.vision_decision_provider import MAX_TEXT_CHARS
from agent.services.vision_decision_task_policy import DEFAULT_VISION_TASK_CATALOG

_REQUIREMENTS = {"acceptance_criteria": ["criterion A text", "criterion B text"]}


class _Service:
    def __init__(self, *, outcome="accepted", answers=None, raises=None):
        self.outcome = outcome
        self.answers = answers or {}
        self.raises = raises
        self.seen = []

    def decide(self, area, request, **kwargs):
        self.seen.append(request)
        if self.raises is not None:
            raise self.raises
        result = SimpleNamespace(answers=dict(self.answers)) if self.outcome == "accepted" else None
        trace = (SimpleNamespace(outcome=self.outcome if self.outcome != "accepted" else "accepted"),)
        outcome = SimpleNamespace(
            result=result,
            trace=trace,
            disagreed=self.outcome == "disagreement",
            deferred=result is None,
            decided_by="fake" if result is not None else None,
        )
        return SimpleNamespace(area=area, mode="active", outcome=outcome, usable=result is not None)


def _answer(value):
    return SimpleNamespace(probability=1.0 if value else 0.0)


def _producer(service):
    return DecisionReviewVerdictProducer(service=service)


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
    service = _Service(
        answers={"criterion_0": _answer(True), "criterion_1": _answer(True), "findings_reported": _answer(False)}
    )
    _producer(service).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    request = service.seen[0]
    assert [q.key for q in request.questions] == ["criterion_0", "criterion_1", "findings_reported"]
    assert all(q.kind == "noul" for q in request.questions)
    assert "criterion A text" in request.questions[0].instructions
    assert request.purpose == "self_development_review"


def test_agreed_true_verdict_approves():
    service = _Service(
        answers={"criterion_0": _answer(True), "criterion_1": _answer(True), "findings_reported": _answer(False)}
    )
    result = _producer(service).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="clean")
    assert result == AcceptanceReviewResult(approved=True, criteria_passed=(True, True), findings=())


def test_agreed_false_criterion_blocks_without_extra_finding():
    service = _Service(
        answers={"criterion_0": _answer(True), "criterion_1": _answer(False), "findings_reported": _answer(False)}
    )
    result = _producer(service).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert result == AcceptanceReviewResult(approved=False, criteria_passed=(True, False), findings=())


def test_reported_findings_block_an_otherwise_clean_verdict():
    service = _Service(
        answers={"criterion_0": _answer(True), "criterion_1": _answer(True), "findings_reported": _answer(True)}
    )
    result = _producer(service).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert result == AcceptanceReviewResult(approved=False, criteria_passed=(True, True), findings=(FINDING_REPORTED,))


def test_engine_disagreement_blocks_the_acceptance():
    result = _producer(_Service(outcome="disagreement")).produce(
        requirements=_REQUIREMENTS, changed_files={}, analysis="a convincing lie"
    )
    assert result == AcceptanceReviewResult(
        approved=False, criteria_passed=(False, False), findings=(FINDING_DISAGREEMENT,)
    )


def test_low_confidence_blocks_the_acceptance():
    result = _producer(_Service(outcome="low_confidence")).produce(
        requirements=_REQUIREMENTS, changed_files={}, analysis="..."
    )
    assert result == AcceptanceReviewResult(
        approved=False, criteria_passed=(False, False), findings=(FINDING_ABSTAINED,)
    )


def test_technical_failures_fail_closed():
    from agent.services.decision_providers.types import DecisionProviderError

    with pytest.raises(AcceptanceReviewError) as excinfo:
        _producer(_Service(raises=DecisionProviderError("timeout", retryable=True))).produce(
            requirements=_REQUIREMENTS, changed_files={}, analysis="..."
        )
    assert excinfo.value.reason_code == "review_verdict_decision_failed"

    with pytest.raises(AcceptanceReviewError) as excinfo:
        _producer(_Service(outcome="error")).produce(requirements=_REQUIREMENTS, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_decision_failed"

    service = _Service()
    with pytest.raises(AcceptanceReviewError) as excinfo:
        _producer(service).produce(requirements={}, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_criteria_mismatch"
    assert service.seen == []

    huge = {"acceptance_criteria": [f"c{i}" for i in range(32)]}
    with pytest.raises(AcceptanceReviewError) as excinfo:
        _producer(service).produce(requirements=huge, changed_files={}, analysis="...")
    assert excinfo.value.reason_code == "review_verdict_schema_too_large"
    assert service.seen == []


def test_factory_activates_via_the_decision_area(monkeypatch):
    class _AreaService:
        def __init__(self, mode):
            self.mode = mode

        def area_mode(self, area):
            assert area == "self_development_review"
            return self.mode

    active = _AreaService("active")
    monkeypatch.setattr("agent.services.decision_providers.service.get_decision_service", lambda: active)
    producer = build_review_verdict_producer()
    assert isinstance(producer, DecisionReviewVerdictProducer)
    assert producer._service is active


def test_factory_is_inactive_without_the_area_opt_in(monkeypatch):
    class _AreaService:
        def __init__(self, mode):
            self.mode = mode

        def area_mode(self, area):
            return self.mode

    monkeypatch.setattr("agent.services.decision_providers.service.get_decision_service", lambda: _AreaService("off"))
    assert build_review_verdict_producer() is None
    monkeypatch.setattr(
        "agent.services.decision_providers.service.get_decision_service", lambda: _AreaService("shadow")
    )
    assert build_review_verdict_producer() is None
    monkeypatch.setattr("agent.services.decision_providers.service.get_decision_service", lambda: None)
    assert build_review_verdict_producer() is None
