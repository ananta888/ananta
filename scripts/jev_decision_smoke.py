#!/usr/bin/env python3
"""Smoke test of a llama-server with the parallel-decision endpoint (JEVCPP-002).

Checks that ``POST /v1/decision`` answers every field with exactly one allowed
value (or a typed error), including candidates that share a token prefix,
and that the normal chat endpoint still works next to it. Prints one JSON
summary; exit code 0 only when every check passed.

    python3 scripts/jev_decision_smoke.py --url http://127.0.0.1:18150
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

SCHEMA = {
    "queue": {"type": "enum", "choices": ["billing", "technical", "account"],
              "description": "Which team should handle this ticket?"},
    "refund": {"type": "boolean", "description": "Is the customer asking for a refund?"},
    "urgency": {"type": "integer", "minimum": 0, "maximum": 3, "description": "How urgent, 0 routine .. 3 critical?"},
    # Candidates that share leading tokens and only diverge later.
    "action": {"type": "enum", "choices": ["refund_full", "refund_partial", "refund_denied", "escalate"],
               "description": "What should support do?"},
}
CONTEXT = "Support ticket: I was charged twice for the same subscription this month. Please refund the duplicate today."


def allowed(field: dict[str, Any]) -> list[Any]:
    if field["type"] == "enum":
        return list(field["choices"])
    if field["type"] == "boolean":
        return [True, False]
    return list(range(field["minimum"], field["maximum"] + 1))


def post(url: str, path: str, body: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any], float]:
    request = urllib.request.Request(url.rstrip("/") + path, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), time.time() - started
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
        except ValueError:
            payload = {}
        return error.code, payload, time.time() - started


def check_decision(url: str, timeout: float) -> dict[str, Any]:
    status, payload, elapsed = post(url, "/v1/decision", {"schema": SCHEMA, "contexts": [CONTEXT],
                                                          "return_probs": True}, timeout)
    if status != 200:
        typed = isinstance(payload.get("error"), dict) and bool(payload["error"].get("type") or payload["error"].get("code"))
        return {"ok": False, "typed_error": typed, "status": status, "error": payload.get("error")}
    result = (payload.get("results") or [{}])[0]
    fields = result.get("fields") or {}
    checks = {}
    for name, spec in SCHEMA.items():
        value = (fields.get(name) or {}).get("value")
        checks[name] = {"value": value, "allowed": value in allowed(spec),
                        "probability": (fields.get(name) or {}).get("probability"),
                        "margin": (fields.get(name) or {}).get("margin")}
    return {"ok": all(item["allowed"] for item in checks.values()), "fields": checks,
            "timings": payload.get("timings"), "usage": payload.get("usage"), "seconds": round(elapsed, 2)}


def check_invalid_schema(url: str, timeout: float) -> dict[str, Any]:
    status, payload, _elapsed = post(url, "/v1/decision", {"schema": {"x": {"type": "enum", "choices": [], "description": "Pick one."}},
                                                           "contexts": ["x"]}, timeout)
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    return {"ok": status == 400 and bool(error), "status": status, "error": error}


def check_chat(url: str, timeout: float) -> dict[str, Any]:
    status, payload, elapsed = post(url, "/v1/chat/completions", {
        "messages": [{"role": "user", "content": "Reply with the single word: pong"}], "max_tokens": 8,
        "temperature": 0}, timeout)
    text = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content") if status == 200 else None
    return {"ok": status == 200 and isinstance(text, str), "status": status, "text": text, "seconds": round(elapsed, 2)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", default="http://127.0.0.1:8096")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)
    summary = {
        "url": args.url,
        "decision": check_decision(args.url, args.timeout),
        "invalid_schema": check_invalid_schema(args.url, args.timeout),
        "chat": check_chat(args.url, args.timeout),
    }
    summary["ok"] = all(summary[key]["ok"] for key in ("decision", "invalid_schema", "chat"))
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
