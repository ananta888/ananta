"""Decision-area review verdicts: mapping, evidence trace, command matching."""

from types import SimpleNamespace

import pytest

from agent.services.self_development.acceptance_review import AcceptanceReviewError
from agent.services.self_development.review_verdict_decision import (
    DecisionReviewVerdictProducer,
    evidence_from_decision,
)
from agent.services.self_development.review_workflow import _normalize_command

CRITERIA = ["c one", "c two"]


def _answer(probability, confidence=0.5):
    return SimpleNamespace(probability=probability, confidence=confidence, choice=None, score=None)


def _decision(answers, *, disagreed=False, deferred=False, mode="active", trace_outcomes=("accepted",)):
    result = SimpleNamespace(answers=answers) if answers is not None else None
    trace = tuple(SimpleNamespace(outcome=outcome) for outcome in trace_outcomes)
    outcome = SimpleNamespace(
        result=result,
        trace=trace,
        disagreed=disagreed,
        deferred=deferred,
        decided_by="local_decision",
    )
    return SimpleNamespace(
        area="self_development_review", mode=mode, outcome=outcome, usable=(mode == "active" and not deferred)
    )


class _Service:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def decide(self, area, request, **kwargs):
        self.requests.append((area, request))
        return self.decision


def _producer(decision):
    return DecisionReviewVerdictProducer(service=_Service(decision))


def _requirements():
    return {"acceptance_criteria": CRITERIA}


def test_agreed_probabilities_produce_binding_verdict():
    decision = _decision(
        {
            "criterion_0": _answer(0.9),
            "criterion_1": _answer(0.8),
            "findings_reported": _answer(0.1),
        }
    )
    result = _producer(decision).produce(requirements=_requirements(), changed_files={}, analysis="all satisfied")
    assert result.approved is True
    assert result.criteria_passed == (True, True)
    assert result.findings == ()


def test_flagged_findings_veto_approval():
    decision = _decision(
        {
            "criterion_0": _answer(0.9),
            "criterion_1": _answer(0.9),
            "findings_reported": _answer(0.7),
        }
    )
    result = _producer(decision).produce(requirements=_requirements(), changed_files={}, analysis="fine, but...")
    assert result.approved is False
    assert result.criteria_passed == (True, True)


def test_disagreement_fails_closed_with_disagreement_finding():
    decision = _decision(None, disagreed=True, trace_outcomes=("disagreement",))
    result = _producer(decision).produce(requirements=_requirements(), changed_files={}, analysis="x")
    assert result.approved is False
    assert result.criteria_passed == (False, False)
    assert result.findings == ("review_verdict_decision_disagreed",)


def test_deferred_fails_closed_with_unconfirmed_finding():
    decision = _decision(None, deferred=True, trace_outcomes=("low_confidence",))
    result = _producer(decision).produce(requirements=_requirements(), changed_files={}, analysis="x")
    assert result.findings == ("review_verdict_decision_unconfirmed",)


def test_missing_criteria_rejects_with_error_and_evidence():
    producer = _producer(_decision({}))
    with pytest.raises(AcceptanceReviewError):
        producer.produce(requirements={"acceptance_criteria": []}, changed_files={}, analysis="x")
    assert producer.last_evidence["verdict_error"] == "review_verdict_criteria_mismatch"


def test_evidence_from_decision_is_bounded_and_redaction_free():
    decision = _decision(
        {
            "criterion_0": _answer(0.91234, 0.61111),
            "findings_reported": _answer(0.1, 0.8),
        }
    )
    evidence = evidence_from_decision(decision)
    trace = evidence["verdict_decision"]
    assert trace["mode"] == "active"
    assert trace["decided_by"] == "local_decision"
    assert trace["answers"]["criterion_0"] == {"probability": 0.9123, "confidence": 0.6111}
    assert evidence_from_decision(None) == {"verdict_decision": "unavailable"}


def test_normalize_command_collapses_whitespace():
    assert (
        _normalize_command("python  -m pytest -q\ttests/self_development")
        == "python -m pytest -q tests/self_development"
    )
    assert _normalize_command(None) == ""
    assert _normalize_command("a b") == _normalize_command(" a   b ")
