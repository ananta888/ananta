"""Record of the decision-based tool choice next to what the companion really did (JEVCPP-006/009).

Per reply one JSON line: route, router-forced calls, the model's own calls,
and the decision (``tool_decision.ToolDecider``) with its probability. Without
the fast path the question is decided after the reply, on a daemon thread, and
only recorded; with the fast path (``MEET_TOOL_DECISION_FAST``) the decision
it already made is recorded, with ``mode`` "fast". The records calibrate and
check the fast path.

On when a decision URL is set (``tool_decision.URL_ENV``). Records go to
``MEET_TOOL_DECISION_SHADOW_LOG`` (default ``/state/tool-decision-shadow.jsonl``)
and hold no question or argument text: only hashes, lengths and whether the
generated argument equals the one that ran.
"""

import hashlib
import json
import os
import threading
import time

from ananta_contracts.tool_decision import CALL, NO_TOOL
from worker.meet_media.tool_decision import decider_from_env

LOG_ENV = "MEET_TOOL_DECISION_SHADOW_LOG"
DEFAULT_LOG = "/state/tool-decision-shadow.jsonl"
DEFAULT_TOOL = "codecompass_search"  # CodeCompassTool calls carry no "tool" key


def _normalized(text):
    return " ".join(str(text or "").split()).casefold()


def first_argument(calls):
    """The argument of the first call that ran (forced calls first); tools record it as ``query``."""
    ordered = [call for call in calls if call.get("forced")] + [call for call in calls if not call.get("forced")]
    return str(ordered[0].get("query") or "") if ordered else ""


def call_names(calls):
    """``(forced, own)`` tool names of one reply, in call order."""
    forced, own = [], []
    for call in calls:
        name = str(call.get("tool") or DEFAULT_TOOL)[:64]
        (forced if call.get("forced") else own).append(name)
    return forced, own


class JsonLinesSink:
    """Appends one JSON object per line; a failed write is dropped, never raised."""

    def __init__(self, path):
        self._path = path
        self._lock = threading.Lock()

    def write(self, record):
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        try:
            with self._lock, open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line)
        except OSError:
            pass


class ToolDecisionShadow:
    def __init__(self, decider, sink, *, wall=time.time):
        self._decider = decider
        self._sink = sink
        self._wall = wall

    def record(self, question, outcome, *, route, calls, mode="shadow"):
        """Write one record for a decided question next to what the reply did; returns the record."""
        forced, own = call_names(calls)
        record = {
            "schema": "ananta.meet_tool_decision_shadow.v1",
            "mode": mode,
            "at": round(self._wall(), 3),
            "question_sha256": hashlib.sha256(str(question or "").encode("utf-8")).hexdigest()[:16],
            "question_chars": len(str(question or "")),
            "route": str(route or ""),
            "forced": forced,
            "model_calls": own,
            "model_first": own[0] if own else NO_TOOL,
            # The reference: the first call that ran (the router's and the fast path's forced calls run first).
            "actual_first": (forced or own or [NO_TOOL])[0],
            "decision_ms": outcome.ms,
        }
        decision = outcome.decision
        if decision is None:
            record.update(decision=None, error=outcome.error)
        else:
            tool = decision.tool or NO_TOOL
            record.update(decision=tool, status=decision.status, reason=decision.reason,
                          probability=round(decision.confidence, 6), margin=decision.margin,
                          agrees=tool == record["actual_first"])
            generated = [decision.arguments.get(name) for name in decision.generated_arguments]
            argument = generated[0] if generated and isinstance(generated[0], str) else ""
            record["argument_chars"] = len(argument)
            if argument and record["agrees"] and decision.status == CALL:
                record["argument_equal"] = _normalized(argument) == _normalized(first_argument(calls))
        self._sink.write(record)
        return record

    def observe(self, question, definitions, *, route, calls):
        """Decide ``question`` now and record it (the shadow mode)."""
        return self.record(question, self._decider.decide(question, definitions), route=route, calls=calls)

    def observe_later(self, question, definitions, *, route, calls):
        """``observe`` on a daemon thread, so the reply is sent without waiting for the shadow."""
        thread = threading.Thread(
            target=self.observe, args=(question, list(definitions)),
            kwargs={"route": route, "calls": [dict(call) for call in calls]},
            name="tool-decision-shadow", daemon=True,
        )
        thread.start()
        return thread


def from_env(environ=None, decider=None):
    """The configured shadow (sharing ``decider`` when given), or ``None`` without a decision URL."""
    environ = os.environ if environ is None else environ
    decider = decider if decider is not None else decider_from_env(environ)
    if decider is None:
        return None
    return ToolDecisionShadow(decider, JsonLinesSink(str(environ.get(LOG_ENV) or DEFAULT_LOG)))
