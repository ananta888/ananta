"""Shadow of decision-based tool choice for the companion (JEVCPP-006/009).

After a reply, the question is scored once more with the llama.cpp
``POST /v1/decision`` endpoint: "which of the offered tools, or none?". The
result is only recorded next to what really happened (route, router-forced
calls, the model's own first call); it never changes a reply, a tool call or
its timing. The records calibrate the fast tool-choice path before anything
relies on it.

Off unless ``MEET_TOOL_DECISION_SHADOW_URL`` names the server (base URL, no
path). Records go to ``MEET_TOOL_DECISION_SHADOW_LOG`` (JSON lines, default
``/state/tool-decision-shadow.jsonl``) and hold no question text: only its
hash and length, so meeting content does not end up in the calibration log.

Independent of the Hub's ``agent.services.tiny_router.parallel_decision``
adapter on purpose: this image does not ship the Hub package.
"""

import hashlib
import json
import math
import os
import threading
import time
import urllib.request

from worker.meet_media.bounded_json_http import BoundedJsonClient, NoRedirect, endpoint

URL_ENV = "MEET_TOOL_DECISION_SHADOW_URL"
LOG_ENV = "MEET_TOOL_DECISION_SHADOW_LOG"
DEFAULT_LOG = "/state/tool-decision-shadow.jsonl"
DECISION_PATH = "/v1/decision"
BUDGET_SECONDS = 10.0
NO_TOOL = "none"
TOOL_FIELD = "tool"
DEFAULT_TOOL = "codecompass_search"  # CodeCompassTool calls carry no "tool" key
INSTRUCTIONS = (
    "Decide how an agent should handle the user's request with the tools below. "
    "For the tool field choose the single best tool, or 'none' when no tool is needed."
)


def decision_schema(definitions):
    """One enum field: the offered tool names plus ``none``, described by the tool descriptions."""
    names, descriptions = [], []
    for definition in definitions:
        function = definition.get("function") or {}
        name = str(function.get("name") or "").strip()
        if name and name != NO_TOOL and name not in names:
            names.append(name)
            descriptions.append(f"{name}: {str(function.get('description') or '').strip()[:200]}")
    if not names:
        raise ValueError("tool_decision_no_tools")
    return {TOOL_FIELD: {
        "type": "enum",
        "choices": [*names, NO_TOOL],
        "description": ("Which tool should handle the request? " + " | ".join(descriptions))[:1500],
    }}


def read_decision(response, choices):
    """``(tool, probability, margin)`` from a decision response; anything malformed raises."""
    results = response.get("results") if isinstance(response, dict) else None
    if not isinstance(results, list) or len(results) != 1:
        raise ValueError("tool_decision_results_invalid")
    field = ((results[0] or {}).get("fields") or {}).get(TOOL_FIELD)
    if not isinstance(field, dict) or field.get("value") not in choices:
        raise ValueError("tool_decision_value_invalid")
    probability = field.get("probability")
    if isinstance(probability, bool) or not isinstance(probability, (int, float)) \
            or not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError("tool_decision_probability_invalid")
    margin = field.get("margin")
    margin = float(margin) if isinstance(margin, (int, float)) and not isinstance(margin, bool) \
        and math.isfinite(margin) else None
    return field["value"], float(probability), margin


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
    def __init__(self, client, sink, *, clock=time.monotonic, wall=time.time, model=""):
        self._client = client
        self._sink = sink
        self._clock = clock
        self._wall = wall
        self._model = model

    def observe(self, question, definitions, *, route, calls):
        """Score ``question`` and record it next to what the reply did; returns the record."""
        forced, own = call_names(calls)
        record = {
            "schema": "ananta.meet_tool_decision_shadow.v1",
            "at": round(self._wall(), 3),
            "question_sha256": hashlib.sha256(str(question or "").encode("utf-8")).hexdigest()[:16],
            "question_chars": len(str(question or "")),
            "route": str(route or ""),
            "forced": forced,
            "model_calls": own,
            "model_first": own[0] if own else NO_TOOL,
            # The reference: the first call that ran (the router's forced calls run first).
            "actual_first": (forced or own or [NO_TOOL])[0],
        }
        started = self._clock()
        try:
            schema = decision_schema(definitions)
            response = self._client.exchange(DECISION_PATH, {
                "model": self._model, "instructions": INSTRUCTIONS, "schema": schema,
                "contexts": [str(question or "")], "mode": "tree", "cache_context": False,
            }, BUDGET_SECONDS)
            tool, probability, margin = read_decision(response, schema[TOOL_FIELD]["choices"])
            record.update(decision=tool, probability=round(probability, 6), margin=margin,
                          agrees=tool == record["actual_first"])
        except ValueError as error:
            record.update(decision=None, error=str(error)[:80])
        record["decision_ms"] = round((self._clock() - started) * 1000.0, 1)
        self._sink.write(record)
        return record

    def observe_later(self, question, definitions, *, route, calls):
        """``observe`` on a daemon thread, so the reply is sent without waiting for the shadow."""
        thread = threading.Thread(
            target=self.observe, args=(question, list(definitions)),
            kwargs={"route": route, "calls": [dict(call) for call in calls]},
            name="tool-decision-shadow", daemon=True,
        )
        thread.start()
        return thread


def from_env(environ=None):
    """The configured shadow, or ``None`` when it is off or its URL is invalid."""
    environ = os.environ if environ is None else environ
    value = str(environ.get(URL_ENV) or "").strip()
    if not value:
        return None
    try:
        base_url = endpoint(value)
    except ValueError:
        return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    return ToolDecisionShadow(
        BoundedJsonClient(base_url, opener=opener),
        JsonLinesSink(str(environ.get(LOG_ENV) or DEFAULT_LOG)),
        model=str(environ.get("MEET_LLM_MODEL") or ""),
    )
