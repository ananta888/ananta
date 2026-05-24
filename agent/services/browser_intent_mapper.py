from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrowserIntentMapping:
    action: str | None
    needs_review: bool
    reason: str


_MAPPING = {
    "navigate": "navigate",
    "open": "navigate",
    "click": "click",
    "type": "type",
    "extract": "extract",
    "screenshot": "screenshot",
}


def map_intent_to_browser_action(intent: str) -> BrowserIntentMapping:
    key = str(intent or "").strip().lower()
    if not key:
        return BrowserIntentMapping(None, True, "browser_intent_missing")
    if key in _MAPPING:
        return BrowserIntentMapping(_MAPPING[key], False, "ok")
    return BrowserIntentMapping(None, True, "browser_intent_ambiguous_needs_review")
