"""Companion routing via the local decision server, next to the keyword rules (MEET_ROUTE_DECISION)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker.meet_media import route_decision as rd
from worker.meet_media.companion_router import classify

pytestmark = pytest.mark.timeout(30)


class _Client:
    def __init__(self, route="ananta_code_architecture", p_route=0.97, p_true=0.95, fail=False):
        self.route, self.p_route, self.p_true, self.fail, self.bodies = route, p_route, p_true, fail, []

    def exchange(self, path, payload, budget):
        self.bodies.append((path, payload))
        if self.fail:
            raise ValueError("transport_failed")
        others = [r for r in rd.ROUTE_OPTIONS if r != self.route]
        return {"results": [{"fields": {
            "route": {"value": self.route, "probs": [{"value": self.route, "probability": self.p_route}]
                      + [{"value": r, "probability": (1 - self.p_route) / 3} for r in others]},
            "knowledge": {"value": self.p_true >= 0.5, "probs": [{"value": True, "probability": self.p_true},
                                                                 {"value": False, "probability": 1 - self.p_true}]},
        }}]}


def _routing(mode="active", **client):
    return rd.DecidedRouting(rd.RouteDecider(_Client(**client)), mode=mode)


def test_the_request_is_one_bounded_decision_with_route_and_knowledge():
    client = _Client()
    rd.RouteDecider(client).decide("wo ist der cirkuit braker")
    path, body = client.bodies[0]
    assert path == "/v1/decision" and body["cache_context"] is False and body["return_probs"] is True
    assert body["schema"]["properties"]["route"]["enum"] == list(rd.ROUTE_OPTIONS)
    assert body["schema"]["properties"]["knowledge"]["type"] == "boolean"


def test_active_mode_uses_a_confident_decision_and_keeps_the_rules_otherwise():
    text = "wo ist der cirkuit braker"  # the rules miss the typo
    rules = classify(text)
    decided = _routing()(text, rules, codecompass_enabled=True)
    assert decided.route == "ananta_code_architecture" and decided.knowledge and decided.codecompass
    assert decided.query  # a search term for the forced lookup
    assert _routing(p_route=0.6)(text, rules, codecompass_enabled=True) is rules  # not confident
    assert _routing(mode="shadow")(text, rules, codecompass_enabled=True) is rules  # shadow never changes
    assert _routing(fail=True)(text, rules, codecompass_enabled=True) is rules  # failure: the rules
    assert not _routing()(text, rules, codecompass_enabled=False).codecompass  # CodeCompass off stays off


def test_configuration_from_the_environment():
    assert rd.routing_from_env({}) is None
    assert rd.routing_from_env({"MEET_ROUTE_DECISION": "active"}) is None  # no URL
    assert rd.routing_from_env({"MEET_ROUTE_DECISION": "off", "MEET_TOOL_DECISION_URL": "http://127.0.0.1:1"}) is None
    routing = rd.routing_from_env({"MEET_ROUTE_DECISION": "active", "MEET_TOOL_DECISION_URL": "http://127.0.0.1:1",
                                   "MEET_ROUTE_DECISION_MIN_CONFIDENCE": "0.5"})
    assert routing is not None and routing._min_confidence == 0.9  # the threshold can only be raised


def test_the_dialog_takes_the_routing_port_and_survives_its_failure():
    from worker.meet_media.companion_dialog import CompanionDialog

    def boom(text, rules, *, codecompass_enabled):
        raise RuntimeError("x")

    dialog = CompanionDialog(llm=lambda text, context, system: "Hallo!", routing=boom)
    reply, trace = dialog.answer("Hallo zusammen")
    assert reply == "Hallo!" and trace.route == "general_question"


def test_the_route_options_are_the_benchmarked_ones():
    question = json.loads((Path(__file__).resolve().parents[2] / "benchmarks/decision_providers/"
                           "companion_route.v1.json").read_text(encoding="utf-8"))["question"]
    assert question["options"] == rd.ROUTE_OPTIONS
