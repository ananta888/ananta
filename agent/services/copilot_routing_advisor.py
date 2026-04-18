from __future__ import annotations

import json
from typing import Any

from agent.services.hub_llm_service import get_hub_llm_service


def build_copilot_routing_prompt(
    *,
    task: dict[str, Any],
    task_kind: str | None,
    required_capabilities: list[str] | None,
    workers: list[dict[str, Any]],
) -> str:
    worker_rows = []
    for worker in workers:
        execution_limits = dict(worker.get("execution_limits") or {})
        worker_rows.append(
            {
                "url": worker.get("url"),
                "status": worker.get("status"),
                "worker_roles": list(worker.get("worker_roles") or []),
                "capabilities": list(worker.get("capabilities") or []),
                "current_load": worker.get("current_load"),
                "max_parallel_tasks": execution_limits.get("max_parallel_tasks"),
                "success_rate": dict(worker.get("routing_signals") or {}).get(
                    "success_rate",
                    worker.get("success_rate", dict(worker.get("metrics") or {}).get("success_rate")),
                ),
                "quality_rate": dict(worker.get("routing_signals") or {}).get(
                    "quality_rate",
                    worker.get("quality_rate", dict(worker.get("metrics") or {}).get("quality_rate")),
                ),
                "security_level": worker.get("security_level") or worker.get("security_tier"),
                "registration_validated": worker.get("registration_validated"),
                "available_for_routing": worker.get("available_for_routing"),
            }
        )
    prompt_payload = {
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "description": task.get("description"),
            "task_kind": task_kind or task.get("task_kind"),
            "required_capabilities": list(required_capabilities or []),
        },
        "workers": worker_rows,
        "instructions": {
            "goal": "Gib nur einen strategischen Routing-Hinweis fuer den Hub. Du delegierst keine Ausfuehrung selbst.",
            "constraints": [
                "Antworte ausschliesslich als JSON.",
                "Waehle suggested_worker_url nur aus der uebergebenen Worker-Liste.",
                "Wenn kein klarer Hinweis moeglich ist, setze suggested_worker_url auf null.",
                "Der Hinweis dient nur als Advisor fuer Routing/Governance und ersetzt keine Policy-Entscheidung.",
            ],
            "response_schema": {
                "suggested_worker_url": "string|null",
                "reasoning": "string",
                "confidence": "number_between_0_and_1",
            },
        },
    }
    return json.dumps(prompt_payload, ensure_ascii=True, indent=2)


def extract_copilot_routing_hint(raw_text: str, worker_urls: list[str]) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    suggested_worker_url = payload.get("suggested_worker_url")
    if suggested_worker_url is not None:
        suggested_worker_url = str(suggested_worker_url).strip() or None
    known_urls = {str(url).strip() for url in worker_urls if str(url).strip()}
    if suggested_worker_url and suggested_worker_url not in known_urls:
        suggested_worker_url = None

    reasoning = str(payload.get("reasoning") or "").strip() or None
    try:
        confidence = float(payload.get("confidence")) if payload.get("confidence") is not None else None
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None:
        confidence = max(0.0, min(1.0, confidence))

    return {
        "suggested_worker_url": suggested_worker_url,
        "reasoning": reasoning,
        "confidence": confidence,
        "raw_response": text,
    }


class CopilotRoutingAdvisor:
    """Kapselt die Copilot-Routing-Advisor-Logik: Prompt-Bau, LLM-Aufruf, Normalisierung."""

    def resolve_routing_hint(
        self,
        *,
        task: dict[str, Any],
        workers: list[dict[str, Any]],
        task_kind: str | None,
        required_capabilities: list[str] | None,
    ) -> dict[str, Any] | None:
        hub_llm = get_hub_llm_service()
        copilot_config = hub_llm.resolve_copilot_config()
        if not copilot_config.get("active") or not copilot_config.get("supports_routing"):
            return None
        prompt = build_copilot_routing_prompt(
            task=task,
            task_kind=task_kind,
            required_capabilities=required_capabilities,
            workers=workers,
        )
        try:
            result = hub_llm.route_with_copilot(prompt=prompt)
        except Exception:
            return None
        hint = extract_copilot_routing_hint(
            str(result.get("text") or ""),
            worker_urls=[str(worker.get("url") or "") for worker in workers],
        )
        if not hint:
            return None
        return {
            **hint,
            "strategy_mode": copilot_config.get("strategy_mode"),
            "effective_provider": dict(copilot_config.get("effective") or {}).get("provider"),
            "effective_model": dict(copilot_config.get("effective") or {}).get("model"),
        }


_copilot_routing_advisor = CopilotRoutingAdvisor()


def get_copilot_routing_advisor() -> CopilotRoutingAdvisor:
    return _copilot_routing_advisor
