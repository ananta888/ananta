"""Decision-based tool choice for the companion (JEVCPP): one ``/v1/decision`` call per question.

The schema and the strict reading of the answer come from
``ananta_contracts.tool_decision`` (shared with the Hub's tiny-router
adapter); this module adds the worker's transport (``BoundedJsonClient``: no
redirects, no proxy, bounded body, redacted errors) and the settings.

- ``ToolDecider.decide`` asks once and never raises: a failure is an outcome
  with an error code.
- ``FastToolChoice`` is the fast path: it passes on a tool call only when the
  decision is confident (``MEET_TOOL_DECISION_MIN_CONFIDENCE``, default 0.85,
  calibrated in docs/jev-llamacpp-decision-mode.md); anything else leaves the
  choice to the model.

Settings: ``MEET_TOOL_DECISION_URL`` (base URL of the llama-server; the older
``MEET_TOOL_DECISION_SHADOW_URL`` still counts), ``MEET_TOOL_DECISION_FAST``
(default off), ``MEET_TOOL_DECISION_ARGUMENT`` (generate a tool's single free
string argument, default on; older name ``MEET_TOOL_DECISION_SHADOW_ARGUMENT``).
"""

import os
import time
import urllib.request
from dataclasses import dataclass

from ananta_contracts.tool_decision import (
    CALL,
    DECISION_PATH,
    build_tool_decision_schema,
    read_tool_decision,
    request_body,
)
from worker.meet_media.bounded_json_http import BoundedJsonClient, NoRedirect, endpoint

URL_ENV = "MEET_TOOL_DECISION_URL"
LEGACY_URL_ENV = "MEET_TOOL_DECISION_SHADOW_URL"
FAST_ENV = "MEET_TOOL_DECISION_FAST"
MIN_CONFIDENCE_ENV = "MEET_TOOL_DECISION_MIN_CONFIDENCE"
ARGUMENT_ENV = "MEET_TOOL_DECISION_ARGUMENT"
LEGACY_ARGUMENT_ENV = "MEET_TOOL_DECISION_SHADOW_ARGUMENT"
DEFAULT_MIN_CONFIDENCE = 0.85
BUDGET_SECONDS = 4.0  # the fast path sits in front of the reply
_FALSE = frozenset({"0", "false", "off", "no"})


@dataclass(frozen=True)
class DecisionOutcome:
    """One asked question: the decision, or the error code that replaced it, and how long it took."""

    decision: object = None  # ananta_contracts.tool_decision.ToolDecision
    error: str = ""
    ms: float = 0.0


class ToolDecider:
    def __init__(self, client, *, model="", open_field=True, budget=BUDGET_SECONDS, clock=time.monotonic):
        self._client = client
        self._model = model
        self._open_field = open_field
        self._budget = budget
        self._clock = clock

    def decide(self, question, definitions):
        """The decision for ``question`` over the offered tools (no confidence cut here)."""
        started = self._clock()
        try:
            schema = build_tool_decision_schema(list(definitions), open_field=self._open_field)
            response = self._client.exchange(
                DECISION_PATH, request_body(schema, str(question or ""), model_id=self._model), self._budget)
            decision, error = read_tool_decision(response, schema, min_confidence=0.0), ""
        except ValueError as failure:  # contract errors and redacted transport failures are ValueErrors
            decision, error = None, str(failure)[:80]
        return DecisionOutcome(decision, error, round((self._clock() - started) * 1000.0, 1))


class FastToolChoice:
    """Callable port for the dialog: a confident tool call, or ``None`` so the model decides."""

    def __init__(self, decider, *, min_confidence=DEFAULT_MIN_CONFIDENCE):
        self._decider = decider
        self._min_confidence = float(min_confidence)
        self.last = None  # the outcome of the latest question, for the shadow record

    def __call__(self, question, definitions):
        self.last = self._decider.decide(question, definitions)
        decision = self.last.decision
        if decision is not None and decision.status == CALL and decision.confidence >= self._min_confidence:
            return decision
        return None


def _flag(environ, name, default):
    return str(environ.get(name, default)).strip().lower() not in _FALSE


def decider_from_env(environ=None):
    """The configured decider, or ``None`` when no valid URL is set."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(URL_ENV) or environ.get(LEGACY_URL_ENV) or "").strip()
    if not value:
        return None
    try:
        base_url = endpoint(value)
    except ValueError:
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    argument = environ.get(ARGUMENT_ENV, environ.get(LEGACY_ARGUMENT_ENV, "1"))
    return ToolDecider(BoundedJsonClient(base_url, opener=opener), model=str(environ.get("MEET_LLM_MODEL") or ""),
                       open_field=str(argument).strip().lower() not in _FALSE)


def fast_choice_from_env(decider, environ=None):
    """The fast path when ``MEET_TOOL_DECISION_FAST`` is on and a decider exists, else ``None``."""
    environ = os.environ if environ is None else environ
    if decider is None or not _flag(environ, FAST_ENV, "0"):
        return None
    try:
        threshold = float(environ.get(MIN_CONFIDENCE_ENV, DEFAULT_MIN_CONFIDENCE))
    except ValueError:
        threshold = DEFAULT_MIN_CONFIDENCE
    return FastToolChoice(decider, min_confidence=min(1.0, max(DEFAULT_MIN_CONFIDENCE, threshold)))
