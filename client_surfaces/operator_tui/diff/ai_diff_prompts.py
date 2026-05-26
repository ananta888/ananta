from __future__ import annotations

import json
from typing import Any


_PROMPTS: dict[str, dict[str, str]] = {
    "review": {
        "control": "Return strict JSON only.",
        "task": "Review the diff context and list concrete findings.",
        "output_schema": "ai_diff_response.v1",
    },
    "explain": {
        "control": "Return strict JSON only.",
        "task": "Explain key code changes and intent.",
        "output_schema": "ai_diff_response.v1",
    },
    "risk": {
        "control": "Return strict JSON only.",
        "task": "Assess risk and regressions from the diff.",
        "output_schema": "ai_diff_response.v1",
    },
    "tests": {
        "control": "Return strict JSON only.",
        "task": "Propose targeted tests for changed behavior.",
        "output_schema": "ai_diff_response.v1",
    },
    "patch": {
        "control": "Return strict JSON only.",
        "task": "Provide patch suggestions only, no auto-apply.",
        "output_schema": "ai_diff_response.v1",
    },
    "chat": {
        "control": "Return strict JSON only.",
        "task": "Answer operator diff questions concisely.",
        "output_schema": "ai_diff_response.v1",
    },
}


def get_ai_diff_prompt_template(mode: str) -> dict[str, str]:
    key = str(mode).strip().lower()
    return dict(_PROMPTS.get(key) or _PROMPTS["review"])


def render_ai_diff_prompt(*, mode: str, context_envelope: dict[str, Any]) -> str:
    prompt = get_ai_diff_prompt_template(mode)
    diff_context = json.dumps(dict(context_envelope), ensure_ascii=False, sort_keys=True)
    return (
        f"CONTROL:\n{prompt['control']}\n\n"
        f"TASK:\n{prompt['task']}\n\n"
        "DIFF_CONTEXT:\n"
        f"{diff_context}\n\n"
        "CODECOMPASS_CONTEXT:\nnone\n\n"
        f"OUTPUT_SCHEMA:\n{prompt['output_schema']}\n"
    )

