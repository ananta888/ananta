from __future__ import annotations

import shlex
import shutil
import time
from dataclasses import dataclass
from typing import Any, Callable

from agent.services.browser_policy_service import get_browser_policy_service
from agent.services.browser_task_contract import BrowserTaskContract


@dataclass(frozen=True)
class BrowserAdapterPreflight:
    ready: bool
    reason: str


@dataclass(frozen=True)
class BrowserAdapterResult:
    status: str
    failure_class: str | None
    actions_executed: int
    trace: list[dict[str, Any]]
    extracted_data: dict[str, Any]


class BrowserUseExecutionAdapter:
    """Policy-bound browser execution adapter with normalized traces."""

    def preflight(self, cfg: dict[str, Any]) -> BrowserAdapterPreflight:
        enabled = bool(cfg.get("enabled", False))
        if not enabled:
            return BrowserAdapterPreflight(False, "browser_backend_disabled")

        command = str(cfg.get("command") or "").strip()
        if not command:
            return BrowserAdapterPreflight(False, "browser_backend_command_missing")

        exe = shlex.split(command)[0]
        if not shutil.which(exe):
            return BrowserAdapterPreflight(False, "browser_backend_binary_missing")

        return BrowserAdapterPreflight(True, "ok")

    def execute(
        self,
        *,
        start_url: str,
        actions: list[dict[str, Any]],
        contract: BrowserTaskContract,
        action_executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> BrowserAdapterResult:
        policy = get_browser_policy_service()
        allow_domain = policy.enforce_domain(url=start_url, contract=contract)
        if not allow_domain.allow:
            return BrowserAdapterResult("blocked", "security_denied", 0, [], {},)

        traces: list[dict[str, Any]] = []
        extracted: dict[str, Any] = {}
        runner = action_executor or (lambda a: {"ok": True, "output": a.get("value")})

        deadline = time.time() + contract.timeout_seconds
        for idx, action in enumerate(actions, start=1):
            budget = policy.enforce_action_budget(action_count=idx, contract=contract)
            if not budget.allow:
                return BrowserAdapterResult("failed", "timeout", idx - 1, traces, extracted)
            if time.time() > deadline:
                return BrowserAdapterResult("failed", "timeout", idx - 1, traces, extracted)

            started = time.time()
            try:
                result = runner(action)
                ok = bool(result.get("ok", False))
                if ok and action.get("type") == "extract" and isinstance(result.get("output"), dict):
                    extracted.update(result.get("output") or {})
                outcome = "ok" if ok else "failed"
            except Exception:
                return BrowserAdapterResult("failed", "transient_navigation", idx - 1, traces, extracted)

            traces.append(
                {
                    "action_type": str(action.get("type") or "unknown"),
                    "target": str(action.get("target") or ""),
                    "outcome": outcome,
                    "duration_ms": int((time.time() - started) * 1000),
                    "policy_decision_ref": "browser-policy-v1",
                }
            )

        return BrowserAdapterResult("success", None, len(actions), traces, extracted)


_SERVICE = BrowserUseExecutionAdapter()


def get_browser_use_execution_adapter() -> BrowserUseExecutionAdapter:
    return _SERVICE
