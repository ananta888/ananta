"""Hub-registered read-only adapter for Ziegler audits."""

from __future__ import annotations

from typing import Any

from flask import Flask

from agent.services.finance_auditor.config import (
    MonetativeAuditorConfig,
    PredatoryDerivativesConfig,
    ZieglerAuditorConfig,
)
from agent.services.finance_auditor.models import ZieglerAuditInput
from agent.services.finance_auditor.service import ZieglerAuditorService
from agent.services.task_handler_registry import register_task_handler


class ZieglerAuditTaskHandler:
    def __init__(
        self,
        config: ZieglerAuditorConfig,
        monetative_config: MonetativeAuditorConfig | None = None,
        predatory_derivatives_config: PredatoryDerivativesConfig | None = None,
    ) -> None:
        self._config = config
        self._service = ZieglerAuditorService(
            config,
            monetative_config=monetative_config,
            predatory_derivatives_config=predatory_derivatives_config,
        )

    def propose(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "proposal_id": "ziegler-audit-proposal",
            "strategy_id": "deterministic_handler",
            "tool_calls": [],
            "command": None,
            "expected_artifacts": [],
            "safety_flags": {"read_only": True, "mutates_filesystem": False},
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        if not self._config.enabled:
            return {"output": "", "exit_code": 1, "error": "ziegler_auditor_disabled", "read_only": True}
        task = kwargs.get("task") or {}
        payload = task.get("audit_input") or task.get("input") or task
        try:
            normalized_payload = dict(payload)
            normalized_payload.setdefault("requested_tone", self._config.tone.value)
            result = self._service.audit(ZieglerAuditInput.from_mapping(normalized_payload))
        except (TypeError, ValueError) as exc:
            return {"output": "", "exit_code": 1, "error": str(exc), "read_only": True}
        return {"output": result.as_dict(), "exit_code": 0, "read_only": True}


def register_ziegler_audit_handler(app: Flask) -> None:
    config = ZieglerAuditorConfig.from_agent_config(app.config.get("AGENT_CONFIG"))
    monetative_config = MonetativeAuditorConfig.from_agent_config(app.config.get("AGENT_CONFIG"))
    predatory_config = PredatoryDerivativesConfig.from_agent_config(app.config.get("AGENT_CONFIG"))
    register_task_handler(
        "ziegler_auditor",
        ZieglerAuditTaskHandler(config, monetative_config, predatory_config),
        app=app,
        capabilities=["finance_analysis", "read_only"],
        safety_flags={"read_only": True, "mutates_filesystem": False, "broker_access": False},
        verification_hooks=["ziegler_audit_contract"],
    )
