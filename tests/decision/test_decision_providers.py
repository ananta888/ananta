"""DPRV: typed decision providers (TypeSafe Jev, local llama.cpp decision, LLM, rules) and the cascade.

Headless: every transport is a fake; no network, no key, no model.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from agent.services.decision_providers import llamacpp, llm
from agent.services.decision_providers import typesafe as ts
from agent.services.decision_providers.cascade import CascadeStage, DecisionCascade
from agent.services.decision_providers.simple import RulesDecisionProvider, StaticDecisionProvider
from agent.services.decision_providers.types import (
    DecisionAnswer,
    DecisionProviderError,
    DecisionQuestion,
    DecisionRequest,
    peak_confidence,
)

pytestmark = pytest.mark.timeout(30)
SECRET = "ts_live_SECRETKEY_0123456789"
REQUEST = DecisionRequest(
    state="Wo wird der CircuitBreaker definiert?",
    questions=(
        DecisionQuestion.choice("tool", "Which tool answers this?", {"search": "find code", "none": "no tool"}),
        DecisionQuestion.score("complexity", "How complex?", ["trivial", "moderate", "hard"]),
        DecisionQuestion.noul("needs_code", "Needs repository code."),
    ),
    purpose="tool_routing",
)
TYPESAFE_OK = {
    "model": "jev-1.13.0",
    "answers": {
        "tool": {"type": "choice", "choice": "search", "confidence": 0.96,
                 "probabilities": {"search": 0.98, "none": 0.02}},
        "complexity": {"type": "score", "score": 0.35, "confidence": 0.48, "legend": {"0": "trivial"},
                       "probabilities": {"0": 0.7, "1": 0.26, "2": 0.04}},
        "needs_code": {"type": "noul", "noul": 0.87},
    },
    "usage": {"input_tokens": 415, "output_tokens": 80},
}


class _Response(io.BytesIO):
    def __init__(self, payload, headers=None):
        super().__init__(json.dumps(payload).encode() if not isinstance(payload, bytes) else payload)
        self.headers = headers or {}
        self.status = 200


class _Opener:
    """Replays a script of responses/exceptions and records every request."""

    def __init__(self, *script):
        self.script, self.requests = list(script), []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step


def _http_error(code, body=b"{}", headers=None):
    return urllib.error.HTTPError("https://api.typesafe.ai/v1/systemone", code, "x", headers or {}, io.BytesIO(body))


def _provider(opener, **kwargs):
    sleeps = []
    provider = ts.TypeSafeJevProvider(api_key=lambda: SECRET, opener=opener, sleep=sleeps.append,
                                      retry=ts.RetryPolicy(max_retries=2, backoff_initial=0.01), **kwargs)
    provider.sleeps = sleeps
    return provider


# --- types -----------------------------------------------------------------------------------------


def test_questions_are_closed_and_validated():
    with pytest.raises(DecisionProviderError):
        DecisionQuestion.choice("tool", "x", {"only": "one"})
    with pytest.raises(DecisionProviderError):
        DecisionQuestion.score("s", "x", [str(i) for i in range(11)])
    with pytest.raises(DecisionProviderError):
        DecisionQuestion("Bad Key", "noul", "x")
    with pytest.raises(DecisionProviderError):
        DecisionRequest(state=" ", questions=REQUEST.questions)
    with pytest.raises(DecisionProviderError):
        DecisionRequest(state="s", questions=(REQUEST.questions[0], REQUEST.questions[0]))


def test_peak_confidence_is_typesafes_statistic():
    assert peak_confidence({"a": 0.5, "b": 0.5}) == 0.0
    assert peak_confidence({"a": 1.0, "b": 0.0, "c": 0.0}) == 1.0
    assert peak_confidence({"a": 0.7, "b": 0.26, "c": 0.04}) == pytest.approx(0.55)


# --- TypeSafe Jev ------------------------------------------------------------------------------------


def test_typesafe_request_and_response_mapping():
    opener = _Opener(_Response(TYPESAFE_OK, {"x-typesafe-request-id": "req_1"}))
    result = _provider(opener).decide(REQUEST)
    request, _timeout = opener.requests[0]
    body = json.loads(request.data)
    assert request.full_url == "https://api.typesafe.ai/v1/systemone"
    assert body["questions"]["tool"] == {"type": "choice", "instructions": "Which tool answers this?",
                                         "criteria": {"search": "find code", "none": "no tool"}}
    assert body["questions"]["complexity"]["criteria"] == ["trivial", "moderate", "hard"]
    assert body["questions"]["needs_code"] == {"type": "noul", "instructions": "Needs repository code."}
    assert request.get_header("Authorization") == f"Bearer {SECRET}" and request.get_header("Idempotency-key")
    tool, complexity, needs = (result.answer(k) for k in ("tool", "complexity", "needs_code"))
    assert (tool.choice, tool.confidence) == ("search", 0.96)
    assert complexity.score == 0.35 and complexity.level == 0
    assert complexity.probabilities == {"trivial": 0.7, "moderate": 0.26, "hard": 0.04}
    assert needs.probability == 0.87 and needs.confidence == pytest.approx(0.74)
    assert result.model == "jev-1.13.0" and result.request_id == "req_1" and result.attempts == 1
    assert result.usage.input_tokens == 415 and result.usage.cost_usd == pytest.approx(415 * 0.042 / 1e6)


@pytest.mark.parametrize("mutate", [
    lambda p: p["answers"]["tool"].update(choice="delete_repo"),  # label outside the schema
    lambda p: p["answers"]["tool"].update(type="score"),
    lambda p: p["answers"].pop("needs_code"),
    lambda p: p["answers"].update(extra={"type": "noul", "noul": 0.1}),
    lambda p: p["answers"]["needs_code"].update(noul=1.7),
    lambda p: p["answers"]["complexity"].update(score=float("nan")),
    lambda p: p["answers"]["tool"]["probabilities"].update(other=0.1),
    lambda p: p.pop("answers"),
])
def test_typesafe_rejects_anything_outside_the_schema(mutate):
    payload = json.loads(json.dumps(TYPESAFE_OK))
    mutate(payload)
    with pytest.raises(DecisionProviderError, match="response_invalid"):
        _provider(_Opener(_Response(payload))).decide(REQUEST)


def test_typesafe_retries_transient_failures_under_one_idempotency_key():
    opener = _Opener(_http_error(429, headers={"retry-after-ms": "5"}), _http_error(529),
                     _Response(TYPESAFE_OK))
    provider = _provider(opener)
    result = provider.decide(REQUEST)
    keys = {request.get_header("Idempotency-key") for request, _ in opener.requests}
    assert result.attempts == 3 and len(keys) == 1
    assert provider.sleeps[0] == pytest.approx(0.005)  # retry-after-ms honoured


@pytest.mark.parametrize("code, reason", [(401, "auth_invalid"), (403, "auth_forbidden"), (422, "request_invalid")])
def test_typesafe_never_retries_client_errors(code, reason):
    opener = _Opener(_http_error(code), _Response(TYPESAFE_OK))
    with pytest.raises(DecisionProviderError, match=reason) as caught:
        _provider(opener).decide(REQUEST)
    assert len(opener.requests) == 1 and caught.value.retryable is False


def test_typesafe_gives_up_after_the_retry_budget():
    opener = _Opener(TimeoutError(), urllib.error.URLError("down"), _http_error(503))
    with pytest.raises(DecisionProviderError, match="http_503") as caught:
        _provider(opener).decide(REQUEST)
    assert len(opener.requests) == 3 and caught.value.retryable


def test_typesafe_respects_one_total_deadline():
    now = [0.0]
    opener = _Opener(_http_error(529), _Response(TYPESAFE_OK))
    provider = ts.TypeSafeJevProvider(api_key=lambda: SECRET, opener=opener, clock=lambda: now[0],
                                      sleep=lambda s: now.__setitem__(0, now[0] + s),
                                      retry=ts.RetryPolicy(max_retries=5, backoff_initial=5.0, backoff_max=5.0))
    with pytest.raises(DecisionProviderError, match="upstream_busy"):
        provider.decide(REQUEST, timeout_seconds=1.0)  # the backoff would pass the deadline
    assert len(opener.requests) == 1
    assert opener.requests[0][1] == pytest.approx(1.0)  # the attempt got the remaining budget


def test_a_missing_key_fails_before_any_request():
    opener = _Opener()
    provider = ts.TypeSafeJevProvider(api_key=lambda: None, opener=opener)
    assert provider.is_available() == (False, "api_key_missing")
    with pytest.raises(DecisionProviderError, match="api_key_missing"):
        provider.decide(REQUEST)
    assert opener.requests == []


def test_the_key_never_leaves_through_repr_errors_or_results():
    opener = _Opener(_http_error(401, body=f'{{"error": "bad key {SECRET}"}}'.encode()))
    provider = _provider(opener)
    with pytest.raises(DecisionProviderError) as caught:
        provider.decide(REQUEST)
    assert SECRET not in repr(provider) and SECRET not in str(caught.value) and SECRET not in repr(caught.value)
    result = _provider(_Opener(_Response(TYPESAFE_OK))).decide(REQUEST)
    assert SECRET not in json.dumps(result.to_mapping())
    assert ts.redact(f"Authorization: Bearer {SECRET}", SECRET) == "Authorization: Bearer [REDACTED]"


def test_the_key_is_read_from_env_or_file(tmp_path):
    assert ts.read_api_key({"TYPESAFE_API_KEY": " k1 "}) == "k1"
    env_file = tmp_path / "api-keys.env"
    env_file.write_text("# secrets\nOTHER=1\nTYPESAFE_API_KEY='k2'\n")
    assert ts.read_api_key({"TYPESAFE_API_KEY_FILE": str(env_file)}) == "k2"
    raw = tmp_path / "key"
    raw.write_text("k3\n")
    assert ts.read_api_key({"TYPESAFE_API_KEY_FILE": str(raw)}) == "k3"
    assert ts.read_api_key({"TYPESAFE_API_KEY_FILE": str(tmp_path / "missing")}) is None
    assert ts.read_api_key({}) is None


def test_the_key_only_travels_over_tls():
    with pytest.raises(DecisionProviderError, match="base_url_invalid"):
        ts.TypeSafeJevProvider(base_url="http://api.typesafe.ai", api_key=lambda: SECRET)
    ts.TypeSafeJevProvider(base_url="http://127.0.0.1:8080", api_key=lambda: SECRET)  # a local compatible server


# --- local llama.cpp decision server ------------------------------------------------------------------


def test_llamacpp_maps_kinds_onto_schema_fields_and_distributions():
    fields = {
        "tool": {"value": "search", "probs": [{"value": "search", "probability": 0.9},
                                              {"value": "none", "probability": 0.1}]},
        "complexity": {"value": "trivial", "probs": [{"value": "trivial", "probability": 0.6},
                                                     {"value": "moderate", "probability": 0.3},
                                                     {"value": "hard", "probability": 0.1}]},
        "needs_code": {"value": True, "probs": [{"value": True, "probability": 0.8},
                                                {"value": False, "probability": 0.2}]},
    }
    opener = _Opener(_Response({"object": "decision", "results": [{"fields": fields}], "model": "bonsai",
                                "usage": {"prompt_tokens": 100, "context_tokens": 20}}))
    result = llamacpp.LlamaCppDecisionProvider(base_url="http://127.0.0.1:18150", opener=opener).decide(REQUEST)
    body = json.loads(opener.requests[0][0].data)
    assert body["schema"]["properties"]["tool"]["enum"] == ["search", "none"]
    assert "search = find code" in body["schema"]["properties"]["tool"]["description"]
    assert body["schema"]["properties"]["needs_code"]["type"] == "boolean"
    assert body["return_probs"] is True and body["cache_context"] is False
    assert result.answer("tool").choice == "search" and result.answer("tool").confidence == pytest.approx(0.8)
    assert result.answer("complexity").score == pytest.approx(0.5)
    assert result.answer("needs_code").probability == pytest.approx(0.8)
    assert result.usage.input_tokens == 120 and result.usage.cost_usd == 0.0


def test_llamacpp_rejects_foreign_values():
    fields = {"tool": {"probs": [{"value": "rm_rf", "probability": 1.0}]}}
    opener = _Opener(_Response({"results": [{"fields": fields}]}))
    request = DecisionRequest(state="s", questions=(REQUEST.questions[0],))
    with pytest.raises(DecisionProviderError, match="response_invalid"):
        llamacpp.LlamaCppDecisionProvider(base_url="http://x", opener=opener).decide(request)


# --- LLM ------------------------------------------------------------------------------------------------


class _Completion:
    def __init__(self, text):
        self.text, self.prompts = text, []

    def complete(self, prompt, *, timeout_seconds):
        self.prompts.append(prompt)
        return self.text, 300, 40


def test_llm_answers_are_validated_like_every_provider():
    text = ('Sure:\n{"tool": {"value": "search", "confidence": 0.9}, "complexity": {"value": "moderate"},'
            ' "needs_code": {"value": "yes", "confidence": 0.6}}')
    result = llm.LLMDecisionProvider(_Completion(text)).decide(REQUEST)
    assert result.answer("tool").choice == "search" and result.answer("tool").confidence == 0.9
    assert result.answer("complexity").score == 1.0
    assert result.answer("needs_code").probability == pytest.approx(0.8)
    with pytest.raises(DecisionProviderError, match="response_invalid"):
        llm.LLMDecisionProvider(_Completion('{"tool": {"value": "shell"}}')).decide(REQUEST)
    with pytest.raises(DecisionProviderError, match="response_invalid"):
        llm.LLMDecisionProvider(_Completion("I think search.")).decide(REQUEST)


# --- cascade ---------------------------------------------------------------------------------------------


def _answers(tool_confidence):
    def answers(request):
        return {"tool": DecisionAnswer("tool", "choice", choice="search",
                                       probabilities={"search": 0.9, "none": 0.1}, confidence=tool_confidence)}
    return answers


ONE_QUESTION = DecisionRequest(state="s", questions=(REQUEST.questions[0],))


def test_the_cascade_accepts_the_first_confident_stage():
    rules = RulesDecisionProvider([lambda request: None])
    jev = StaticDecisionProvider(_answers(0.95), provider_id="typesafe_jev")
    fallback = StaticDecisionProvider(_answers(1.0), provider_id="llm")
    outcome = DecisionCascade([CascadeStage(rules, 1.0), CascadeStage(jev, 0.9), CascadeStage(fallback, 0.5)]).decide(
        ONE_QUESTION)
    assert outcome.decided_by == "typesafe_jev" and fallback.calls == []
    assert [stage.outcome for stage in outcome.trace] == ["error", "accepted"]


def test_the_cascade_falls_back_on_low_confidence_errors_and_missing_keys():
    missing_key = ts.TypeSafeJevProvider(api_key=lambda: None, opener=_Opener())
    unsure = StaticDecisionProvider(_answers(0.6), provider_id="llamacpp_decision")
    failing = StaticDecisionProvider(provider_id="broken", error=DecisionProviderError("timeout", retryable=True))
    fallback = StaticDecisionProvider(_answers(0.99), provider_id="llm")
    outcome = DecisionCascade([CascadeStage(missing_key), CascadeStage(unsure, 0.9), CascadeStage(failing),
                               CascadeStage(fallback, 0.9)]).decide(ONE_QUESTION)
    assert outcome.decided_by == "llm" and outcome.fell_back
    assert [(s.outcome, s.reason) for s in outcome.trace] == [
        ("unavailable", "api_key_missing"), ("low_confidence", ""), ("error", "timeout"), ("accepted", "")]


def test_the_cascade_defers_instead_of_guessing():
    outcome = DecisionCascade([CascadeStage(StaticDecisionProvider(_answers(0.7)), 0.9)]).decide(ONE_QUESTION)
    assert outcome.deferred and outcome.result is None and outcome.to_mapping()["deferred"] is True


def test_per_question_thresholds_are_stricter_where_configured():
    stage = CascadeStage(StaticDecisionProvider(_answers(0.95)), 0.9, {"tool": 0.98})
    assert DecisionCascade([stage]).decide(ONE_QUESTION).deferred


# --- tool choice translation ------------------------------------------------------------------------------


def test_tool_questions_and_reading_match_the_shared_tool_contract():
    from agent.services.decision_providers.tool_choice import read_tool_choice, tool_questions
    from agent.services.decision_providers.types import DecisionResult

    tools = [{"type": "function", "function": {"name": "search", "description": "Find code.", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
             {"type": "function", "function": {"name": "heads", "description": "Index heads.", "parameters": {
                 "type": "object", "properties": {"scope": {"enum": ["repo", "docs"]}}, "required": ["scope"]}}}]
    schema, request, values = tool_questions(tools, "Wo ist X?")
    assert request.question("tool").labels == ("search", "heads", "none")
    assert list(values) == ["arg_0_scope"] and request.question("arg_0_scope").labels == ("repo", "docs")

    def result(tool, tool_conf, scope_conf):
        return DecisionResult("p", "m", {
            "tool": DecisionAnswer("tool", "choice", choice=tool, confidence=tool_conf),
            "arg_0_scope": DecisionAnswer("arg_0_scope", "choice", choice="docs", confidence=scope_conf)})

    call = read_tool_choice(schema, result("search", 0.95, 0.1), values, "Wo ist X?", min_confidence=0.9)
    assert (call.status, call.tool, dict(call.arguments)) == ("call", "search", {"query": "Wo ist X?"})
    heads = read_tool_choice(schema, result("heads", 0.95, 0.93), values, "Heads?", min_confidence=0.9)
    assert heads.status == "call" and dict(heads.arguments) == {"scope": "docs"} and heads.confidence == 0.93
    unsure = read_tool_choice(schema, result("heads", 0.95, 0.5), values, "Heads?", min_confidence=0.9)
    assert unsure.status == "abstain" and unsure.tool == "heads"
    assert read_tool_choice(schema, result("none", 0.97, 0.0), values, "Hi", min_confidence=0.9).status == "respond"


# --- evaluation ----------------------------------------------------------------------------------------


def test_metrics_report_calibration_confident_mistakes_and_cascades():
    from agent.services.decision_providers.evaluation import Run, brier, macro_f1, provider_report, simulate_cascade

    runs = [Run("a", "x", "x", 0.99, latency_ms=100, cost_usd=0.001), Run("b", "y", "x", 0.95, latency_ms=300),
            Run("c", "y", "y", 0.60, latency_ms=200), Run("d", "x", None, 0.0, error="timeout")]
    report = provider_report(runs)
    assert report["accuracy"] == 0.5 and report["errors"] == 1 and report["error_reasons"] == ["timeout"]
    assert report["confident_mistakes"] == {"threshold": 0.9, "count": 1, "cases": ["b"]}
    assert report["latency_ms"]["p50"] == 200 and report["cost_usd"]["total"] == 0.001
    assert [row["threshold"] for row in report["thresholds"]] == [0.7, 0.8, 0.9, 0.95, 0.98]
    assert macro_f1([Run("a", "x", "x", 1), Run("b", "y", "y", 1)]) == 1.0
    assert brier([Run("a", "x", "x", 1.0), Run("b", "x", "y", 1.0)]) == 0.5
    fallback = {r.case_id: Run(r.case_id, r.expected, r.expected, 1.0, latency_ms=1000) for r in runs}
    rows = {row["threshold"]: row for row in simulate_cascade({r.case_id: r for r in runs}, fallback)}
    assert rows[0.9]["fallback_rate"] == 0.5 and rows[0.9]["confident_mistakes"] == 1 and rows[0.9]["accuracy"] == 0.75
    assert rows[0.98]["fallback_rate"] == 0.75 and rows[0.98]["accuracy"] == 1.0


# --- text arguments as candidate choices ---------------------------------------------------------------


def test_prompt_spans_and_symbol_index_produce_candidates():
    from agent.services.decision_providers.argument_candidates import (
        CompositeCandidates,
        PromptSpanCandidates,
        SymbolIndexCandidates,
        split_identifier,
        symbol_names,
    )

    assert split_identifier("CircuitBreakerOpen") == ["circuit", "breaker", "open"]
    assert split_identifier("agent/services/tool_loop.py") == ["agent", "services", "tool", "loop", "py"]
    spans = [c.value for c in PromptSpanCandidates().candidates('Zeig `build_schema` in agent/tools.py und "hac:x1"')]
    assert {"build_schema", "agent/tools.py", "hac:x1"} <= set(spans)
    names = symbol_names([("agent/breaker.py", "class CircuitBreaker:\n    def allow(self):\n"),
                          ("web/app.ts", "export function renderGraph() {}\n")])
    assert names == ["agent/breaker.py", "CircuitBreaker", "allow", "web/app.ts", "renderGraph"]
    index = SymbolIndexCandidates(lambda: names)
    assert index.candidates("wo ist der cirkuit braker")[0].value == "CircuitBreaker"  # typo -> real name
    assert index.candidates("render graph bitte")[0].value == "renderGraph"
    assert index.candidates("völlig anderes thema") == []
    merged = CompositeCandidates([PromptSpanCandidates(), index], limit=3).candidates("CircuitBreaker circuitbreaker")
    assert len(merged) <= 3 and len({c.value.lower() for c in merged}) == len(merged)


def test_the_symbol_index_is_rebuilt_only_when_its_names_change():
    from agent.services.decision_providers.argument_candidates import SymbolIndexCandidates

    lists = {"current": ["AlphaService"]}
    calls = []
    index = SymbolIndexCandidates(lambda: lists["current"])
    index._build = lambda source, original=index._build: (calls.append(1), original(source))[1]
    index.candidates("alpha service")
    index.candidates("alpha service")
    lists["current"] = ["BetaService"]
    assert index.candidates("beta service")[0].value == "BetaService" and len(calls) == 2


def test_the_text_argument_is_a_candidate_choice_in_the_same_request():
    from agent.services.decision_providers.argument_candidates import Candidate
    from agent.services.decision_providers.tool_choice import read_tool_choice, tool_questions
    from agent.services.decision_providers.types import DecisionResult

    class Fixed:
        def candidates(self, prompt, *, tool="", argument=""):
            return [Candidate("CircuitBreaker", "symbol"), Candidate("agent/common/breaker.py", "path")]

    tools = [{"type": "function", "function": {"name": "search", "description": "Find code.", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]
    prompt = "wo ist der cirkuit braker"
    schema, request, values = tool_questions(tools, prompt, candidates=Fixed())
    text_key = next(key for key in values if key.startswith("text_"))
    labels = request.question(text_key).labels
    assert labels == ("CircuitBreaker", "v1", "whole_request", "none_fits")  # a path becomes an opaque label

    def result(choice):
        return DecisionResult("p", "m", {
            "tool": DecisionAnswer("tool", "choice", choice="search", confidence=0.97),
            text_key: DecisionAnswer(text_key, "choice", choice=choice, confidence=0.4)})

    # an unsure text choice never blocks the call: below text_min_confidence the request itself is used
    assert read_tool_choice(schema, result("CircuitBreaker"), values, prompt, min_confidence=0.9).arguments == {
        "query": prompt}
    assert read_tool_choice(schema, result("CircuitBreaker"), values, prompt, min_confidence=0.9,
                            text_min_confidence=0.3).arguments == {"query": "CircuitBreaker"}
    assert read_tool_choice(schema, result("v1"), values, prompt, min_confidence=0.9,
                            text_min_confidence=0.0).arguments == {"query": "agent/common/breaker.py"}
    for fallback in ("whole_request", "none_fits"):
        assert read_tool_choice(schema, result(fallback), values, prompt, min_confidence=0.9).arguments == {
            "query": prompt}


def test_concurrent_first_use_of_the_symbol_index_is_safe():
    import concurrent.futures

    from agent.services.decision_providers.argument_candidates import SymbolIndexCandidates

    names = [f"Service{i}Handler" for i in range(3000)] + ["CircuitBreaker"]
    index = SymbolIndexCandidates(lambda: names)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _i: index.candidates("circuit breaker")[0].value, range(16)))
    assert set(results) == {"CircuitBreaker"}
