"""DPRV: decision provider configuration, the area service and the tiny-router adapter. Headless."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.services.decision_providers import config as dc
from agent.services.decision_providers.service import DecisionService, build_provider
from agent.services.decision_providers.simple import StaticDecisionProvider
from agent.services.decision_providers.types import DecisionAnswer, DecisionQuestion, DecisionRequest

pytestmark = pytest.mark.timeout(30)
JEV_ON = {"enabled": True, "external_calls_allowed": True}
REQUEST = DecisionRequest(state="Wo ist der CircuitBreaker?", questions=(
    DecisionQuestion.choice("tool", "Which tool?", {"search": "find code", "none": "no tool"}),))


def _section(**areas):
    return {"enabled": True, "providers": {"jev": JEV_ON, "llm": {"enabled": True}}, "areas": areas}


# --- config -------------------------------------------------------------------------------------------


def test_everything_is_off_by_default():
    cfg = dc.normalize_decision_config({})
    assert cfg["enabled"] is False and cfg["areas"] == {}
    assert all(not provider["enabled"] for provider in cfg["providers"].values())
    assert dc.area_settings(cfg, "tool_routing") is None
    from agent.config_defaults import build_default_agent_config

    assert build_default_agent_config()["decision_providers"] == dc.DEFAULTS


def test_jev_needs_an_explicit_external_opt_in_and_tls():
    with pytest.raises(dc.DecisionConfigError, match="external_calls_not_allowed:jev"):
        dc.normalize_decision_config({"providers": {"jev": {"enabled": True}}})
    with pytest.raises(dc.DecisionConfigError, match="base_url_invalid:jev"):
        dc.normalize_decision_config({"providers": {"jev": {**JEV_ON, "base_url": "http://api.typesafe.ai"}}})


@pytest.mark.parametrize("raw", [
    {"providers": {"jev": {"api_key": "ts_secret"}}},
    {"areas": {"tool_routing": {"mode": "off", "token": "x"}}},
    {"providers": {"jev": {**JEV_ON, "headers": {"Authorization": "Bearer x"}}}},
])
def test_secrets_are_never_accepted_in_config(raw):
    with pytest.raises(dc.DecisionConfigError):
        dc.normalize_decision_config(raw)


@pytest.mark.parametrize("area, reason", [
    ({"mode": "sometimes"}, "mode_invalid"),
    ({"mode": "active"}, "cascade_required"),
    ({"mode": "active", "provider": "jev", "cascade": ["llm"]}, "provider_and_cascade"),
    ({"mode": "active", "cascade": ["jev", "gpt"]}, "cascade_invalid"),
    ({"mode": "active", "provider": "local_decision"}, "provider_disabled"),
    ({"mode": "active", "provider": "jev", "confidence_threshold": 1.5}, "threshold_invalid"),
])
def test_invalid_areas_are_rejected(area, reason):
    with pytest.raises(dc.DecisionConfigError, match=reason):
        dc.normalize_decision_config(_section(tool_routing=area))


def test_area_settings_merge_the_global_threshold_and_respect_the_kill_switch():
    section = _section(tool_routing={"mode": "shadow", "cascade": ["jev", "llm"]},
                       chat_intent={"mode": "active", "provider": "llm", "confidence_threshold": 0.8})
    tool = dc.area_settings(section, "tool_routing")
    assert tool["cascade"] == ["jev", "llm"] and tool["confidence_threshold"] == 0.9
    assert dc.area_settings(section, "chat_intent")["confidence_threshold"] == 0.8
    assert dc.area_settings({**section, "enabled": False}, "tool_routing") is None
    assert dc.area_settings({**section, "areas": {"tool_routing": {"mode": "bogus"}}}, "tool_routing") is None


def test_updates_merge_over_the_stored_section():
    stored = dc.normalize_decision_config(_section(tool_routing={"mode": "shadow", "provider": "jev"}))
    updated = dc.normalize_decision_config({"areas": {"tool_routing": {"mode": "active"}}}, stored)
    assert updated["areas"]["tool_routing"]["mode"] == "active"
    assert updated["providers"]["jev"]["enabled"] is True and updated["enabled"] is True


def test_the_jev_provider_reads_its_key_from_the_configured_env_only():
    settings = dc.normalize_decision_config({"providers": {"jev": {**JEV_ON, "api_key_env": "ANANTA_JEV_KEY"}}})
    provider = build_provider("jev", settings["providers"]["jev"], environ={"ANANTA_JEV_KEY": "k"})
    assert provider.is_available() == (True, "ok")
    missing = build_provider("jev", settings["providers"]["jev"], environ={"TYPESAFE_API_KEY": "other"})
    assert missing.is_available() == (False, "api_key_missing")


# --- service --------------------------------------------------------------------------------------------


def _answer(confidence):
    return lambda request: {"tool": DecisionAnswer("tool", "choice", choice="search",
                                                   probabilities={"search": 0.9, "none": 0.1},
                                                   confidence=confidence)}


class _Sink:
    def __init__(self):
        self.events = []

    def record(self, event):
        self.events.append(event)


def _service(section, providers):
    sink = _Sink()
    service = DecisionService(section=lambda: section, provider_factory=lambda name, settings: providers[name],
                              sink=sink)
    return service, sink


def test_an_off_area_costs_nothing():
    jev = StaticDecisionProvider(_answer(0.99))
    service, sink = _service(_section(), {"jev": jev})
    assert service.decide("tool_routing", REQUEST) is None and jev.calls == [] and sink.events == []
    assert service.area_mode("tool_routing") == "off"


def test_shadow_decides_and_records_but_is_never_usable():
    service, sink = _service(_section(tool_routing={"mode": "shadow", "provider": "jev"}),
                             {"jev": StaticDecisionProvider(_answer(0.99), provider_id="typesafe_jev")})
    decision = service.decide("tool_routing", REQUEST, current={"tool": "none"})
    assert decision.outcome.decided_by == "typesafe_jev" and decision.usable is False
    event = sink.events[0]
    assert event["mode"] == "shadow" and event["current"] == {"tool": "none"}
    assert "CircuitBreaker" not in str(event) and len(event["state_sha256"]) == 64  # no decision text recorded


def test_active_uses_the_cascade_and_falls_back():
    section = _section(tool_routing={"mode": "active", "cascade": ["jev", "llm"]})
    service, _sink = _service(section, {"jev": StaticDecisionProvider(_answer(0.5), provider_id="typesafe_jev"),
                                        "llm": StaticDecisionProvider(_answer(0.95), provider_id="llm")})
    decision = service.decide("tool_routing", REQUEST)
    assert decision.usable and decision.outcome.decided_by == "llm" and decision.outcome.fell_back
    service, _sink = _service(section, {"jev": StaticDecisionProvider(_answer(0.5)),
                                        "llm": StaticDecisionProvider(_answer(0.6))})
    assert service.decide("tool_routing", REQUEST).usable is False  # deferred: today's path


# --- tiny router adapter -----------------------------------------------------------------------------------


TOOLS = [
    {"type": "function", "function": {"name": "codecompass_search", "description": "Find code in the repository.",
                                      "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                                                     "required": ["query"]}}},
    {"type": "function", "function": {"name": "codecompass_layers_heads", "description": "Current index heads.",
                                      "parameters": {"type": "object", "properties": {
                                          "scope": {"type": "string", "enum": ["repo", "docs"]}},
                                          "required": ["scope"]}}},
]


def _adapter(section, answers):
    from agent.services.tiny_router.decision_provider_adapter import DecisionProviderToolAdapter

    provider = StaticDecisionProvider(answers, provider_id="typesafe_jev")
    service, _sink = _service(section, {"jev": provider})
    return DecisionProviderToolAdapter(service), provider


def _profile(adapter="decision_provider"):
    return SimpleNamespace(adapter=adapter, min_confidence=0.9, model_id="m", metadata={})


def _request(prompt="Wo wird der CircuitBreaker definiert?"):
    from agent.services.tiny_router.types import AdapterRequest

    return AdapterRequest(prompt=prompt, tools=tuple(TOOLS), profile=_profile(), timeout_ms=5000)


def _choose(tool, confidence, scope=("repo", 0.2)):
    def answers(request):
        options = request.question("tool").labels
        result = {"tool": DecisionAnswer("tool", "choice", choice=tool,
                                         probabilities={o: (confidence if o == tool else 0.0) for o in options},
                                         confidence=confidence)}
        for question in request.questions[1:]:
            result[question.key] = DecisionAnswer(question.key, "choice", choice=scope[0],
                                                  probabilities={scope[0]: 1.0}, confidence=scope[1])
        return result
    return answers


def test_the_adapter_is_unavailable_while_the_area_is_off():
    adapter, _provider = _adapter(_section(), _choose("codecompass_search", 0.99))
    assert adapter.is_available(_profile()) == (False, "decision_area_off")
    assert adapter.is_available(_profile("parallel_decision"))[1] == "adapter_profile_mismatch"


def test_the_adapter_calls_a_tool_with_the_prompt_as_its_text_argument():
    section = _section(tool_routing={"mode": "shadow", "provider": "jev"})
    adapter, provider = _adapter(section, _choose("codecompass_search", 0.97))
    assert adapter.is_available(_profile())[0]
    result = adapter.propose(_request())
    call = result.payload["tool_calls"][0]
    assert call["name"] == "codecompass_search"
    assert call["arguments"] == {"query": "Wo wird der CircuitBreaker definiert?"}
    assert result.payload["decided_by"] == "typesafe_jev"
    # the unsure argument question of the other tool did not block the call
    assert [q.key for q in provider.calls[0].questions] == ["tool", "arg_0_scope"]


def test_the_adapter_abstains_on_unsure_arguments_of_the_chosen_tool_and_on_deferrals():
    section = _section(tool_routing={"mode": "shadow", "provider": "jev"})
    adapter, _provider = _adapter(section, _choose("codecompass_layers_heads", 0.97, ("repo", 0.3)))
    payload = adapter.propose(_request("Welche Index-Heads gibt es?")).payload
    assert payload["tool_calls"] == [] and payload["tool_hint"] == "codecompass_layers_heads"
    adapter, _provider = _adapter(section, _choose("codecompass_search", 0.5))
    payload = adapter.propose(_request()).payload
    assert payload["tool_calls"] == [] and payload["reason"] == "decision_deferred"
    adapter, _provider = _adapter(section, _choose("none", 0.99))
    assert adapter.propose(_request("Hallo!")).payload["type"] == "respond"


# --- POST /config ---------------------------------------------------------------------------------------------


def test_post_config_validates_the_section(client, admin_auth_header):
    bad = client.post("/config", json={"decision_providers": {"providers": {"jev": {"enabled": True}}}},
                      headers=admin_auth_header)
    assert bad.status_code == 400 and b"external_calls_not_allowed" in bad.data
    secret = client.post("/config", json={"decision_providers": {"providers": {"jev": {"api_key": "x"}}}},
                         headers=admin_auth_header)
    assert secret.status_code == 400
    ok = client.post("/config", json={"decision_providers": {
        "enabled": True, "providers": {"jev": JEV_ON},
        "areas": {"tool_routing": {"mode": "shadow", "provider": "jev"}}}}, headers=admin_auth_header)
    assert ok.status_code == 200
    stored = client.get("/config", headers=admin_auth_header).get_json()
    stored = stored.get("data", stored)["decision_providers"]
    assert stored["areas"]["tool_routing"] == {"mode": "shadow", "cascade": ["jev"], "question_thresholds": {}}


# --- retrieval intent integration -------------------------------------------------------------------------------


def _intent_service(mode, intent, confidence):
    def answers(request):
        options = request.question("retrieval_intent").labels
        return {"retrieval_intent": DecisionAnswer("retrieval_intent", "choice", choice=intent,
                                                   probabilities={o: (confidence if o == intent else 0.0)
                                                                  for o in options}, confidence=confidence)}
    provider = StaticDecisionProvider(answers, provider_id="typesafe_jev")
    section = _section(retrieval_intent={"mode": mode, "provider": "jev"})
    service, sink = _service(section, {"jev": provider})
    return service, sink, provider


def test_retrieval_intent_keeps_the_rules_unless_an_active_decision_is_accepted():
    from agent.services.retrieval_intent_decision import decide_retrieval_intent

    service, sink, provider = _intent_service("off", "generic_chat", 0.99)
    assert decide_retrieval_intent("wie ist das Wetter", "architecture_overview", {}, service=service) == (
        "architecture_overview", [])
    assert provider.calls == []
    service, sink, _provider = _intent_service("shadow", "generic_chat", 0.99)
    intent, reasons = decide_retrieval_intent("wie ist das Wetter", "architecture_overview", {}, service=service)
    assert intent == "architecture_overview" and reasons == ["decision_provider:shadow:shadow"]
    assert sink.events[0]["current"] == {"retrieval_intent": "architecture_overview"}
    service, _sink, _provider = _intent_service("active", "generic_chat", 0.99)
    intent, reasons = decide_retrieval_intent("wie ist das Wetter", "architecture_overview", {}, service=service)
    assert intent == "generic_chat" and reasons[-1] == "rule_intent:architecture_overview"
    service, _sink, _provider = _intent_service("active", "generic_chat", 0.6)
    assert decide_retrieval_intent("wie ist das Wetter", "architecture_overview", {}, service=service)[0] == (
        "architecture_overview")


def test_explicit_trigger_modes_and_empty_queries_are_never_overridden():
    from agent.services.retrieval_intent_decision import decide_retrieval_intent

    service, _sink, provider = _intent_service("active", "generic_chat", 0.99)
    assert decide_retrieval_intent("x", "implemented_code_explanation", {"chat_codecompass_trigger_mode": "always"},
                                   service=service)[0] == "implemented_code_explanation"
    assert decide_retrieval_intent("  ", "generic_chat", {}, service=service)[0] == "generic_chat"
    assert provider.calls == []


def test_the_production_question_is_the_benchmarked_one():
    import json
    from pathlib import Path

    from agent.services.retrieval_intent_decision import INSTRUCTIONS, INTENT_OPTIONS

    question = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/decision_providers/"
                           "retrieval_intent.v1.json").read_text(encoding="utf-8"))["question"]
    assert question["options"] == INTENT_OPTIONS and question["instructions"] == INSTRUCTIONS


# --- agreement stage and prompt-injection screening -------------------------------------------------------------


def _label(key, label, confidence, options=("benign", "instruction_override")):
    return lambda request: {key: DecisionAnswer(key, "choice", choice=label,
                                                probabilities={o: (confidence if o == label else 0.0) for o in options},
                                                confidence=confidence)}


def test_an_agreement_stage_needs_the_same_confident_answer_from_every_member():
    from agent.services.decision_providers.cascade import AgreementStage, DecisionCascade

    local = StaticDecisionProvider(_label("tool", "search", 0.95), provider_id="llamacpp_decision")
    jev = StaticDecisionProvider(_label("tool", "search", 0.92), provider_id="typesafe_jev")
    outcome = DecisionCascade([AgreementStage([local, jev], 0.9)]).decide(REQUEST)
    assert outcome.decided_by == "agreement(llamacpp_decision+typesafe_jev)"
    assert outcome.result.answer("tool").confidence == 0.92 and outcome.result.answer("tool").choice == "search"
    other = StaticDecisionProvider(_label("tool", "none", 0.99), provider_id="typesafe_jev")
    outcome = DecisionCascade([AgreementStage([local, other], 0.9)]).decide(REQUEST)
    assert outcome.deferred and outcome.disagreed and outcome.trace[0].reason == "tool"
    unsure = StaticDecisionProvider(_label("tool", "search", 0.7), provider_id="typesafe_jev")
    assert DecisionCascade([AgreementStage([local, unsure], 0.9)]).decide(REQUEST).trace[0].outcome == "low_confidence"


def test_an_agreement_stage_fails_closed_on_a_missing_member():
    from agent.services.decision_providers import typesafe as ts
    from agent.services.decision_providers.cascade import AgreementStage, CascadeStage, DecisionCascade

    local = StaticDecisionProvider(_label("tool", "search", 0.99), provider_id="llamacpp_decision")
    no_key = ts.TypeSafeJevProvider(api_key=lambda: None)
    fallback = StaticDecisionProvider(_label("tool", "search", 0.95), provider_id="llm")
    outcome = DecisionCascade([AgreementStage([local, no_key]), CascadeStage(fallback)]).decide(REQUEST)
    assert outcome.decided_by == "llm"
    assert outcome.trace[0].outcome == "unavailable" and "api_key_missing" in outcome.trace[0].reason


def test_config_accepts_agreement_groups_and_builds_them():
    section = _section(prompt_injection={"mode": "shadow", "cascade": [["jev", "llm"]]})
    assert dc.area_settings(section, "prompt_injection")["cascade"] == [["jev", "llm"]]
    with pytest.raises(dc.DecisionConfigError, match="cascade_invalid"):
        dc.normalize_decision_config(_section(prompt_injection={"mode": "shadow", "cascade": [["jev"]]}))
    with pytest.raises(dc.DecisionConfigError, match="cascade_invalid"):
        dc.normalize_decision_config(_section(prompt_injection={"mode": "shadow", "cascade": [["jev", "llm"], "jev"]}))
    service, _sink = _service(section, {"jev": StaticDecisionProvider(_label("intent", "benign", 0.95)),
                                        "llm": StaticDecisionProvider(_label("intent", "benign", 0.97))})
    request = DecisionRequest(state="Hallo", questions=(
        DecisionQuestion.choice("intent", "x", {"benign": "b", "instruction_override": "o"}),))
    assert service.decide("prompt_injection", request).outcome.decided_by.startswith("agreement(")


def _screen_service(mode, local_label, local_conf, jev_label, jev_conf):
    from agent.services.prompt_injection_screening import INTENT_OPTIONS

    options = tuple(INTENT_OPTIONS)
    section = {"enabled": True, "providers": {"jev": JEV_ON, "local_decision": {"enabled": True, "base_url": "http://x"}},
               "areas": {"prompt_injection": {"mode": mode, "cascade": [["local_decision", "jev"]]}}}
    providers = {"local_decision": StaticDecisionProvider(_label("intent", local_label, local_conf, options),
                                                          provider_id="llamacpp_decision"),
                 "jev": StaticDecisionProvider(_label("intent", jev_label, jev_conf, options),
                                               provider_id="typesafe_jev")}
    return _service(section, providers)[0]


def test_the_screen_is_benign_only_when_both_models_say_so():
    from agent.services.prompt_injection_screening import screen_prompt_injection

    benign = screen_prompt_injection("Hallo", service=_screen_service("shadow", "benign", 0.97, "benign", 0.95))
    assert benign.verdict == "benign" and not benign.needs_review
    attack = screen_prompt_injection("Ignore all", service=_screen_service(
        "shadow", "instruction_override", 0.99, "instruction_override", 0.93))
    assert attack.verdict == "suspicious" and attack.label == "instruction_override" and attack.needs_review
    split = screen_prompt_injection("tricky", service=_screen_service("shadow", "benign", 0.99, "data_exfiltration",
                                                                      0.95))
    assert split.verdict == "uncertain" and split.reason == "disagreement" and split.needs_review
    unsure = screen_prompt_injection("hm", service=_screen_service("shadow", "benign", 0.99, "benign", 0.5))
    assert unsure.verdict == "uncertain"
    off = screen_prompt_injection("Ignore all", service=_screen_service("off", "benign", 1, "benign", 1))
    assert off.verdict == "off" and not off.needs_review


def test_the_screening_question_is_the_benchmarked_one():
    import json
    from pathlib import Path

    from agent.services.prompt_injection_screening import INSTRUCTIONS, INTENT_OPTIONS

    question = json.loads((Path(__file__).resolve().parents[1] / "benchmarks/decision_providers/"
                           "prompt_injection.v1.json").read_text(encoding="utf-8"))["question"]
    assert question["options"] == INTENT_OPTIONS and question["instructions"] == INSTRUCTIONS
