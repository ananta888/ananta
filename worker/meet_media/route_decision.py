"""Companion routing via the local decision server (``/v1/decision``), next to the keyword rules.

The keyword router (``companion_router.classify``) is exact on its own phrases but
misroutes ordinary wording (benchmark 2026-09-27, hard set: 36 % route, 59 %
lookup-needed; the local decision mode 96 % / 100 %, ~300 ms). This asks the
same local llama-server that already decides tools for one choice (the route) and
one yes/no field (does the answer need a CodeCompass lookup?).

``MEET_ROUTE_DECISION``: ``off`` (default) | ``shadow`` (decide and log, the rules
win) | ``active`` (a decision at or above ``MEET_ROUTE_DECISION_MIN_CONFIDENCE``,
default 0.90, replaces the rules; otherwise the rules stay). The decision only
routes; retrieval, tools and answers keep their own checks. Uses the tool
decision's endpoint (``MEET_TOOL_DECISION_URL``); no Hub code (worker boundary).
"""

import logging
import os
import time
import urllib.request

from worker.meet_media.bounded_json_http import BoundedJsonClient, NoRedirect, endpoint
from worker.meet_media.companion_router import (
    ANANTA_CODE_ARCHITECTURE,
    GENERAL_QUESTION,
    MEET_RUNTIME_CURRENT_DIALOG,
    SELF_EXPLANATION,
    RouteDecision,
    search_query,
)

MODE_ENV = "MEET_ROUTE_DECISION"
MIN_CONFIDENCE_ENV = "MEET_ROUTE_DECISION_MIN_CONFIDENCE"
URL_ENV = "MEET_TOOL_DECISION_URL"
DEFAULT_MIN_CONFIDENCE = 0.90
BUDGET_SECONDS = 2.0
INSTRUCTIONS = ("Decide where the companion avatar must ground its answer to this meeting chat message, "
                "and whether the answer needs a lookup in the Ananta code knowledge base.")
# Kept identical to benchmarks/decision_providers/companion_route.v1.json (a test checks it).
ROUTE_OPTIONS = {
    SELF_EXPLANATION: "The user asks how the assistant produced its previous answer, where it got it from, or which "
    "sources that last answer used (answered from the recorded trace, no model call).",
    ANANTA_CODE_ARCHITECTURE: "The user asks about Ananta itself: its repository, code, files, modules, classes, "
    "functions, services, endpoints, scripts, CodeCompass, hub/worker architecture, a named identifier or path, or "
    "how the companion itself works internally (voice/speech output, avatar, mouth animation, lip sync, pipeline); "
    "needs a CodeCompass lookup.",
    MEET_RUNTIME_CURRENT_DIALOG: "The user asks about the currently running meeting or call: participants, room, "
    "chat, microphone/audio/video, what is happening right now in this conversation; this also covers questions "
    "about how Ananta Meet works (runtime context, plus code lookup when code is mentioned).",
    GENERAL_QUESTION: "Smalltalk, greetings, jokes, and general world-knowledge questions that concern neither "
    "Ananta's code nor the current meeting; answered by the LLM alone without CodeCompass.",
}
KNOWLEDGE_DESCRIPTION = ("True if answering needs a lookup in the Ananta code knowledge base (CodeCompass): questions "
                         "about Ananta's repository, code, named identifiers or paths, architecture, or the "
                         "companion's own implementation; false for smalltalk, general world knowledge, pure "
                         "meeting-state questions and questions about how the previous answer was produced.")
_log = logging.getLogger(__name__)


def _probability(field, value):
    for item in (field or {}).get("probs") or []:
        if isinstance(item, dict) and item.get("value") == value:
            probability = item.get("probability")
            if isinstance(probability, (int, float)) and not isinstance(probability, bool):
                return float(probability)
    return None


class RouteDecider:
    """One ``/v1/decision`` call: ``(route, knowledge, confidence)`` or ``None`` on any failure."""

    def __init__(self, client, *, model="", budget=BUDGET_SECONDS, clock=time.monotonic):
        self._client = client
        self._model = model
        self._budget = budget
        self._clock = clock
        self.last_ms = 0.0

    def decide(self, text):
        started = self._clock()
        body = {"model": self._model, "instructions": INSTRUCTIONS, "mode": "tree", "return_probs": True,
                "cache_context": False, "contexts": [str(text or "")[:1000]],
                "schema": {"type": "object", "properties": {
                    "route": {"enum": list(ROUTE_OPTIONS), "description": "Where to ground the answer. " + " ".join(
                        f"{key} = {value}" for key, value in ROUTE_OPTIONS.items())},
                    "knowledge": {"type": "boolean", "description": KNOWLEDGE_DESCRIPTION}}}}
        try:
            response = self._client.exchange("/v1/decision", body, self._budget)
            fields = response["results"][0]["fields"]
            route = fields["route"]["value"]
            if route not in ROUTE_OPTIONS:
                return None
            p_route = _probability(fields["route"], route)
            p_true = _probability(fields["knowledge"], True)
            if p_route is None or p_true is None:
                return None
        except (ValueError, KeyError, IndexError, TypeError) as failure:
            _log.debug("route decision failed: %s", str(failure)[:80])
            return None
        finally:
            self.last_ms = round((self._clock() - started) * 1000.0, 1)
        knowledge = p_true >= 0.5
        confidence = min(p_route, max(p_true, 1.0 - p_true))
        return route, knowledge, confidence


class DecidedRouting:
    """Routing port for the dialog: the rules, or a confident decision in active mode."""

    def __init__(self, decider, *, mode="shadow", min_confidence=DEFAULT_MIN_CONFIDENCE):
        self._decider = decider
        self._mode = mode
        self._min_confidence = float(min_confidence)
        self.last = None  # (rules route, decided route/knowledge/confidence, used) for the trace

    def __call__(self, text, rules, *, codecompass_enabled):
        decided = self._decider.decide(text)
        use = (self._mode == "active" and decided is not None and decided[2] >= self._min_confidence)
        self.last = {"rules": rules.route, "decided": decided, "used": use, "ms": self._decider.last_ms}
        if decided is not None and (decided[0], decided[1]) != (rules.route, rules.knowledge):
            _log.info("route decision %s (knowledge=%s, p=%.2f) vs rules %s (knowledge=%s)%s", decided[0],
                      decided[1], decided[2], rules.route, rules.knowledge, " -> used" if use else "")
        if not use:
            return rules
        route, knowledge, confidence = decided
        return RouteDecision(route, bool(knowledge and codecompass_enabled), f"route_decision_{confidence:.2f}",
                             knowledge=bool(knowledge), query=search_query(text) if knowledge else "")


def routing_from_env(environ=None):
    """The routing port for ``MEET_ROUTE_DECISION`` shadow/active with a valid URL, else ``None``."""
    environ = os.environ if environ is None else environ
    mode = str(environ.get(MODE_ENV) or "off").strip().lower()
    value = str(environ.get(URL_ENV) or "").strip()
    if mode not in {"shadow", "active"} or not value:
        return None
    try:
        base_url = endpoint(value)
        threshold = float(environ.get(MIN_CONFIDENCE_ENV, DEFAULT_MIN_CONFIDENCE))
    except ValueError:
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    decider = RouteDecider(BoundedJsonClient(base_url, opener=opener), model=str(environ.get("MEET_LLM_MODEL") or ""))
    return DecidedRouting(decider, mode=mode, min_confidence=min(1.0, max(DEFAULT_MIN_CONFIDENCE, threshold)))
