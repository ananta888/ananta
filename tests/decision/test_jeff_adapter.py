"""Jeff transport/policy regressions, entirely headless and without weights."""

from __future__ import annotations

import io
import json
import urllib.error
from dataclasses import replace

import pytest

from agent.services.decision_providers.cascade import CascadeStage, DecisionCascade
from agent.services.decision_providers.config import normalize_decision_config
from agent.services.decision_providers.jeff import JeffDecisionProvider, validate_local_endpoint
from agent.services.decision_providers.llamacpp import LlamaCppDecisionProvider, parse_answer, request_body
from agent.services.decision_providers.types import DecisionProviderError, DecisionQuestion, DecisionRequest
from worker.decision.jeff_runtime import conservative_token_bound

pytestmark = pytest.mark.timeout(20)
REVISION = "d0a4e53d09cebe6bc963dd9be319d4279084bb2d"
QUESTION = DecisionQuestion.choice("tool", "Choose a tool", {"search": "search code", "no_tool": "none"})
REQUEST = DecisionRequest("Find the implementation", (QUESTION,), tenant_id="tenant-a", subject_id="subject-a")


class Response(io.BytesIO):
    status = 200
    headers = {"x-ananta-model-revision": REVISION}

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())


def provider(opener, **kw):
    return JeffDecisionProvider(
        base_url="http://127.0.0.1:8000",
        model_revision=REVISION,
        api_key=lambda: "test-secret-key-for-transport",
        opener=opener,
        max_retries=0,
        **kw,
    )


def reply(q):
    if q.kind == "choice":
        raw = {
            "type": "choice",
            "choice": "search",
            "confidence": 0.98,
            "probabilities": {"search": 0.99, "no_tool": 0.01},
        }
    elif q.kind == "score":
        raw = {"type": "score", "score": 0.1, "confidence": 0.2, "probabilities": {"0": 0.6, "1": 0.4}}
    else:
        raw = {"type": "noul", "noul": 0.8}
    return {"model": "gliformer-large-v1", "answers": {q.key: raw}, "usage": {"input_tokens": 5}}


def test_isolated_requests_and_score_keeps_raw_expectation():
    questions = (
        QUESTION,
        DecisionQuestion.score("rank", "Priority", ["low", "high"]),
        DecisionQuestion.noul("needed", "Search needed?"),
    )
    calls = []

    def opener(req, timeout):
        body = json.loads(req.data)
        calls.append(body)
        key = next(iter(body["questions"]))
        q = next(q for q in questions if q.key == key)
        return Response(reply(q))

    result = provider(opener).decide(replace(REQUEST, questions=questions))
    assert len(calls) == 3 and all(len(c["questions"]) == 1 for c in calls)
    assert result.answers["rank"].score == 0.1  # display distribution expectation would be 0.4
    assert result.model.endswith("@" + REVISION)
    assert result.usage.input_tokens == 15 and result.usage.cost_usd == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://api.typesafe.ai:443",
        "http://127.0.0.1.evil:8000",
        "http://127.0.0.1:8000?key=secret",
        "http://user:pass@localhost:8000",
        "http://8.8.8.8:8000",
        "http://localhost:8000/redirect",
    ],
)
def test_nonlocal_or_unapproved_endpoints_are_rejected(url):
    with pytest.raises(DecisionProviderError):
        validate_local_endpoint(url, ("127.0.0.1", "localhost", "8.8.8.8"))


@pytest.mark.parametrize("status", [401, 422])
def test_client_errors_are_not_retried(status):
    calls = []

    def opener(req, timeout):
        calls.append(req)
        raise urllib.error.HTTPError(req.full_url, status, "ignored", {}, io.BytesIO(b"secret prompt"))

    with pytest.raises(DecisionProviderError) as exc:
        provider(opener).decide(REQUEST)
    assert len(calls) == 1 and "secret" not in str(exc.value)


def test_circuit_breaker_and_admission_are_bounded():
    def failing(req, timeout):
        raise urllib.error.URLError("sensitive details")

    p = provider(failing, failure_limit=1)
    with pytest.raises(DecisionProviderError, match="transport_error"):
        p.decide(REQUEST)
    assert p.is_available() == (False, "circuit_open")
    p = provider(lambda req, timeout: Response(reply(QUESTION)), max_inflight=1)
    assert p._slots.acquire(False)
    try:
        with pytest.raises(DecisionProviderError, match="queue_full"):
            p.decide(REQUEST)
    finally:
        p._slots.release()
    assert p.decide(REQUEST).answers["tool"].choice == "search"


def test_uncertain_answer_defers_without_granting_any_permission():
    response = reply(QUESTION)
    response["answers"]["tool"]["confidence"] = 0.1
    outcome = DecisionCascade([CascadeStage(provider(lambda req, timeout: Response(response)), 0.9)]).decide(REQUEST)
    assert outcome.deferred and outcome.result is None


@pytest.mark.parametrize("mode", ["unknown", "sequential", "grouped"])
def test_dependent_requests_do_not_reach_parallel_transports(mode):
    dependent = replace(REQUEST, questions=(replace(QUESTION, dependency_mode=mode),))
    with pytest.raises(DecisionProviderError, match="require_system2"):
        request_body(dependent)
    with pytest.raises(DecisionProviderError, match="require_system2"):
        provider(lambda *a, **kw: pytest.fail("transport called")).decide(dependent)


@pytest.mark.parametrize("values", [[0.9, float("nan")], [0.9, -0.1], [0.9, 1.1], [0.1, 0.1]])
def test_llamacpp_bad_distributions_are_never_renormalized(values):
    raw = {"probs": [{"value": label, "probability": p} for label, p in zip(QUESTION.labels, values)]}
    with pytest.raises(DecisionProviderError):
        parse_answer(QUESTION, raw)


def test_batch_rejects_cross_tenant_and_does_not_cache_context():
    bodies = []

    def opener(req, timeout):
        bodies.append(json.loads(req.data))
        row = {
            "fields": {
                "tool": {"probs": [{"value": "search", "probability": 0.9}, {"value": "no_tool", "probability": 0.1}]}
            }
        }
        return Response({"model": "tiny", "results": [row, row], "usage": {"prompt_tokens": 100}})

    p = LlamaCppDecisionProvider(base_url="http://localhost:8000", opener=opener)
    with pytest.raises(DecisionProviderError, match="scope_mismatch"):
        p.decide_batch((REQUEST, replace(REQUEST, tenant_id="tenant-b")))
    assert not bodies
    results = p.decide_batch((REQUEST, replace(REQUEST, state="Another state")))
    assert len(results) == 2 and sum(r.usage.input_tokens for r in results) == 100
    assert bodies[0]["cache_context"] is False and bodies[0]["cache_prompt"] is False


def test_jeff_config_off_does_not_construct_or_download_a_model():
    assert normalize_decision_config({})["providers"]["jeff"]["enabled"] is False


def test_encoder_bound_includes_schema_and_instructions():
    assert (
        conservative_token_bound(
            {"state": "a", "model": "gliformer", "questions": {"x": {"criteria": {"a": "b", "c": "d"}}}}
        )
        > 300
    )
    assert (
        conservative_token_bound(
            {"state": "a", "model": "gliformer", "questions": {"x": {"criteria": {"a": "b" * 1000}}}}
        )
        > 1000
    )


def test_missing_runtime_revision_cannot_be_presented_as_pinned_model():
    response = Response(reply(QUESTION))
    response.headers = {}
    with pytest.raises(DecisionProviderError, match="model_revision_unverified"):
        provider(lambda req, timeout: response).decide(REQUEST)


def test_prefix_cache_scope_is_bound_to_tenant_subject_schema_and_model():
    first = request_body(REQUEST, "model-a", scoped_prefix_cache=True)
    same = request_body(replace(REQUEST, state="new context"), "model-a", scoped_prefix_cache=True)
    assert first["cache_scope"] == same["cache_scope"]
    assert first["cache_prompt"] is True and first["cache_context"] is False
    for changed in (replace(REQUEST, tenant_id="other"), replace(REQUEST, subject_id="other")):
        assert request_body(changed, "model-a", scoped_prefix_cache=True)["cache_scope"] != first["cache_scope"]
    assert request_body(REQUEST, "model-b", scoped_prefix_cache=True)["cache_scope"] != first["cache_scope"]
    with pytest.raises(DecisionProviderError, match="scope_required"):
        request_body(replace(REQUEST, tenant_id=""), scoped_prefix_cache=True)
