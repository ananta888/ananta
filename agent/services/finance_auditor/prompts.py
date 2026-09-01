"""Optional LLM prompt port; deterministic analysis never depends on it."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from agent.services.finance_auditor.models import ZieglerAuditInput


class FinanceAuditLlmPort(Protocol):
    def analyze(self, prompt: str) -> dict[str, Any]: ...


_PROMPT_PATH = Path(__file__).parents[3] / "prompts" / "ziegler_finance_auditor.j2"
_MONETATIVE_PROMPT_PATH = Path(__file__).parents[3] / "prompts" / "monetative_money_auditor.j2"
_DERIVATIVES_PROMPT_PATH = Path(__file__).parents[3] / "prompts" / "predatory_derivatives_auditor.j2"


def render_prompt(audit_input: ZieglerAuditInput, deterministic_result: dict[str, Any]) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{{ claim }}", audit_input.claim).replace(
        "{{ deterministic_result }}", str(deterministic_result)
    )


def render_monetative_prompt(claim: str, deterministic_result: dict[str, Any]) -> str:
    template = _MONETATIVE_PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{{ claim }}", claim).replace("{{ deterministic_result }}", str(deterministic_result))


def render_derivatives_prompt(claim: str, deterministic_result: dict[str, Any]) -> str:
    template = _DERIVATIVES_PROMPT_PATH.read_text(encoding="utf-8")
    return template.replace("{{ claim }}", claim).replace("{{ deterministic_result }}", str(deterministic_result))
