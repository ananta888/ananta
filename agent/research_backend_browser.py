"""Native browser research backends (browser_use, Camofox).

Executes a policy-bounded browser task described by the research context and
returns the research-backend triple ``(returncode, stdout, stderr)``. Every
policy decision is audited through the injected audit sink; nothing here waits
for a human — review needs are returned as a bounded
``browser_needs_review:{...}`` result.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from agent.common.audit import log_audit
from agent.services.browser_artifact_service import get_browser_artifact_service
from agent.services.browser_camofox_adapter import build_camofox_adapter
from agent.services.browser_policy_service import get_browser_policy_service
from agent.services.browser_recovery_service import get_browser_recovery_service
from agent.services.browser_task_contract import BrowserTaskContract
from agent.services.browser_use_adapter import get_browser_use_execution_adapter

BROWSER_POLICY_VERSION = "browser-policy-v1"

BROWSER_OBSERVABILITY: dict[str, Any] = {
    "calls": 0,
    "actions": 0,
    "last_failure_class": None,
    "last_latency_ms": 0,
}
CAMOFOX_OBSERVABILITY: dict[str, Any] = {
    "calls": 0,
    "actions": 0,
    "last_failure_class": None,
    "last_latency_ms": 0,
}

BackendResult = tuple[int, str, str]
AuditSink = Callable[[str, dict], Any]


class _CamofoxActionFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _contract_payload(browser_cfg: dict, timeout: int | None, cfg: dict) -> dict[str, Any]:
    return {
        "allowed_domains": browser_cfg.get("allowed_domains") or [],
        "max_actions": browser_cfg.get("max_actions") or 10,
        "timeout_seconds": browser_cfg.get("timeout_seconds") or int(timeout or cfg["timeout_seconds"]),
        "download_policy": browser_cfg.get("download_policy") or "deny",
        "auth_policy": browser_cfg.get("auth_policy") or "none",
        "screenshot_policy": browser_cfg.get("screenshot_policy") or "none",
        "download_allowlist": browser_cfg.get("download_allowlist") or [],
        "output_dir": browser_cfg.get("output_dir"),
    }


def _needs_review_result(policy_reason: str, failure_class: str, artifact_payload: dict) -> BackendResult:
    escalation = {
        "status": "needs_review",
        "policy_reason": policy_reason,
        "failure_class": failure_class,
        "evidence_refs": list(artifact_payload.get("sources") or []),
    }
    return -1, "", f"browser_needs_review:{json.dumps(escalation, ensure_ascii=False)}"


def _max_repair_attempts(browser_cfg: dict) -> int:
    raw_max_repairs = browser_cfg.get("max_repair_attempts")
    try:
        return int(raw_max_repairs) if raw_max_repairs is not None else 1
    except Exception:
        return 1


class BrowserUseResearchRunner:
    """browser_use: preflight, auth policy, execution, artifact gate and recovery decision."""

    def __init__(self, audit: AuditSink = log_audit) -> None:
        self._audit = audit

    def run(self, cfg: dict, *, timeout: int | None, research_context: dict[str, Any] | None) -> BackendResult:
        started = time.time()
        ctx = dict(research_context or {})
        browser_cfg = dict(ctx.get("browser_config") or {})
        contract = BrowserTaskContract.from_payload(_contract_payload(browser_cfg, timeout, cfg))
        adapter = get_browser_use_execution_adapter()
        preflight = adapter.preflight(browser_cfg)
        self._audit_preflight(preflight.ready)
        if not preflight.ready:
            BROWSER_OBSERVABILITY["calls"] += 1
            BROWSER_OBSERVABILITY["last_failure_class"] = "backend_unavailable"
            self._audit("browser_policy_blocked", {"provider": "browser_use", "reason": preflight.reason})
            return -1, "", preflight.reason

        start_url = str(ctx.get("start_url") or "").strip()
        actions = list(ctx.get("actions") or [])
        if not start_url:
            return -1, "", "browser_use_start_url_missing"
        auth_blocked = self._auth_blocked(browser_cfg, contract)
        if auth_blocked is not None:
            return auth_blocked

        result = adapter.execute(start_url=start_url, actions=actions, contract=contract)
        self._record_execution(result, started)
        artifact_payload = {
            "extracted_data": dict(result.extracted_data or {}),
            "page_evidence": [{"url": start_url, "action_count": result.actions_executed}],
            "sources": [{"url": start_url, "kind": "web"}],
            "trace": list(result.trace or []),
        }
        max_repair_attempts = _max_repair_attempts(browser_cfg)
        self._audit_download_actions(actions)
        check = get_browser_artifact_service().validate_schema(artifact_payload)
        if not check.valid:
            self._audit("browser_policy_blocked", {"provider": "browser_use", "reason": check.reason})
            return -1, "", check.reason
        if result.status == "success":
            return self._success(artifact_payload, browser_cfg, actions, max_repair_attempts)
        return self._recovery(result, artifact_payload, browser_cfg, max_repair_attempts)

    def _audit_preflight(self, ready: bool) -> None:
        self._audit(
            "browser_route_selected",
            {
                "provider": "browser_use",
                "resolved_backend": "browser_use",
                "reason": "research_backend_policy:research->browser_use",
                "ready": ready,
            },
        )
        self._audit(
            "browser_policy_checked",
            {"provider": "browser_use", "phase": "preflight", "ready": ready, "policy_version": BROWSER_POLICY_VERSION},
        )

    def _auth_blocked(self, browser_cfg: dict, contract: Any) -> BackendResult | None:
        if not bool(browser_cfg.get("auth_requested", False)):
            return None
        if contract.auth_policy != "explicit_opt_in":
            self._audit(
                "browser_policy_blocked",
                {"provider": "browser_use", "reason": "browser_policy_auth_not_allowed", "auth_policy": "masked"},
            )
            return -1, "", "browser_policy_auth_not_allowed"
        self._audit(
            "browser_policy_checked",
            {
                "provider": "browser_use",
                "phase": "auth",
                "auth_requested": True,
                "auth_policy": "masked",
                "policy_version": BROWSER_POLICY_VERSION,
            },
        )
        return None

    def _record_execution(self, result: Any, started: float) -> None:
        latency_ms = int((time.time() - started) * 1000)
        BROWSER_OBSERVABILITY["calls"] += 1
        BROWSER_OBSERVABILITY["actions"] += int(result.actions_executed)
        BROWSER_OBSERVABILITY["last_latency_ms"] = latency_ms
        BROWSER_OBSERVABILITY["last_failure_class"] = result.failure_class
        self._audit(
            "browser_action_executed",
            {
                "provider": "browser_use",
                "status": result.status,
                "failure_class": result.failure_class,
                "actions_executed": result.actions_executed,
                "latency_ms": latency_ms,
            },
        )

    def _audit_download_actions(self, actions: list) -> None:
        for action in actions:
            if str((action or {}).get("type") or "").strip().lower() != "download":
                continue
            self._audit(
                "browser_policy_checked",
                {
                    "provider": "browser_use",
                    "phase": "download",
                    "policy_version": BROWSER_POLICY_VERSION,
                    "download_url": str((action or {}).get("url") or ""),
                    "output_path": str((action or {}).get("output_path") or ""),
                    "provenance_ref": "browser-policy-v1:download",
                },
            )

    def _success(
        self, artifact_payload: dict, browser_cfg: dict, actions: list, max_repair_attempts: int
    ) -> BackendResult:
        force_review = (
            max_repair_attempts <= 0
            and bool(browser_cfg.get("fallback_allowed", True))
            and len(actions) == 1
            and str((actions[0] or {}).get("type") or "").strip().lower() == "extract"
        )
        if force_review:
            self._audit(
                "browser_fallback_used",
                {
                    "provider": "browser_use",
                    "reason": "browser_repair_budget_exhausted",
                    "action": "needs_review",
                    "policy_version": BROWSER_POLICY_VERSION,
                },
            )
            return _needs_review_result("browser_repair_budget_exhausted", "repair_budget_exhausted", artifact_payload)
        gate = get_browser_artifact_service().verify_completion_gate(
            payload=artifact_payload,
            min_source_count=int(browser_cfg.get("min_source_count") or 1),
            require_evidence=bool(browser_cfg.get("require_evidence", False)),
        )
        if not gate.valid:
            self._audit(
                "browser_policy_blocked",
                {"provider": "browser_use", "reason": gate.reason, "policy_version": BROWSER_POLICY_VERSION},
            )
            return -1, "", gate.reason
        self._audit("browser_artifact_verified", {"provider": "browser_use", "status": "passed"})
        return 0, json.dumps(artifact_payload, ensure_ascii=False), ""

    def _recovery(
        self, result: Any, artifact_payload: dict, browser_cfg: dict, max_repair_attempts: int
    ) -> BackendResult:
        failure_class = str(result.failure_class or "transient_navigation")
        recovery = get_browser_recovery_service().decide(
            failure_class=failure_class,
            attempt=1,
            max_repair_attempts=max_repair_attempts,
            fallback_allowed=bool(browser_cfg.get("fallback_allowed", True)),
            strict_browser_evidence=bool(browser_cfg.get("strict_browser_evidence", False)),
        )
        if recovery.action in {"needs_review", "fail"}:
            self._audit(
                "browser_fallback_used",
                {
                    "provider": "browser_use",
                    "reason": recovery.reason,
                    "action": recovery.action,
                    "policy_version": BROWSER_POLICY_VERSION,
                },
            )
        if recovery.action == "needs_review":
            return _needs_review_result(recovery.reason, failure_class, artifact_payload)
        return -1, "", f"browser_use_{result.failure_class or 'failed'}:{recovery.action}:{recovery.reason}"


class CamofoxResearchRunner:
    """Camofox: health check, auth policy, bounded session with navigate + actions, artifact check."""

    def __init__(self, audit: AuditSink = log_audit) -> None:
        self._audit = audit

    def run(self, cfg: dict, *, timeout: int | None, research_context: dict[str, Any] | None) -> BackendResult:
        started = time.time()
        ctx = dict(research_context or {})
        browser_cfg = dict(ctx.get("browser_config") or {})
        if not cfg["enabled"]:
            return -1, "", "Camofox research backend is disabled"

        contract = BrowserTaskContract.from_payload(
            {
                **_contract_payload(browser_cfg, timeout, cfg),
                "persist_session": bool(browser_cfg.get("persist_session", False)),
                "blocked_domains": browser_cfg.get("blocked_domains"),
            }
        )
        adapter = build_camofox_adapter(browser_cfg)
        health = adapter.health_check()
        self._audit(
            "browser_route_selected",
            {"provider": "camofox", "resolved_backend": "camofox", "healthy": health.get("healthy")},
        )
        if not health.get("healthy"):
            CAMOFOX_OBSERVABILITY["calls"] += 1
            CAMOFOX_OBSERVABILITY["last_failure_class"] = "backend_unavailable"
            self._audit("browser_policy_blocked", {"provider": "camofox", "reason": "camofox_server_unavailable"})
            return -1, "", f"camofox_server_unavailable:{health.get('error', '')}"

        start_url = str(ctx.get("start_url") or "").strip()
        actions = list(ctx.get("actions") or [])
        if not start_url:
            return -1, "", "camofox_start_url_missing"

        get_browser_policy_service()
        if bool(browser_cfg.get("auth_requested", False)) and contract.auth_policy != "explicit_opt_in":
            self._audit("browser_policy_blocked", {"provider": "camofox", "reason": "browser_policy_auth_not_allowed"})
            return -1, "", "browser_policy_auth_not_allowed"

        failure, extracted, actions_executed, session_id = self._run_session(adapter, contract, start_url, actions)
        if failure is not None:
            return failure

        CAMOFOX_OBSERVABILITY["calls"] += 1
        CAMOFOX_OBSERVABILITY["actions"] += actions_executed
        CAMOFOX_OBSERVABILITY["last_latency_ms"] = int((time.time() - started) * 1000)
        CAMOFOX_OBSERVABILITY["last_failure_class"] = None

        artifact_payload = {
            "extracted_data": extracted,
            "page_evidence": [{"url": start_url, "action_count": actions_executed}],
            "sources": [{"url": start_url, "kind": "web"}],
            "trace": [{"provider": "camofox", "session_id": session_id, "actions_executed": actions_executed}],
        }
        check = get_browser_artifact_service().validate_schema(artifact_payload)
        if not check.valid:
            self._audit("browser_policy_blocked", {"provider": "camofox", "reason": check.reason})
            return -1, "", check.reason
        self._audit("browser_artifact_verified", {"provider": "camofox", "status": "passed"})
        return 0, json.dumps(artifact_payload, ensure_ascii=False), ""

    def _run_action(
        self, adapter: Any, action: dict, *, start_url: str, session_id: str, contract: Any, extracted: dict
    ) -> None:
        action_type = str((action or {}).get("type") or "").strip().lower()
        if action_type == "read":
            res = adapter.read_page(session_id=session_id, contract=contract)
            if res.ok:
                extracted.update(res.data)
        elif action_type == "click":
            adapter.click(selector=str(action.get("selector") or ""), session_id=session_id, contract=contract)
        elif action_type == "type":
            adapter.type_text(
                selector=str(action.get("selector") or ""),
                text=str(action.get("text") or ""),
                session_id=session_id,
                contract=contract,
            )
        elif action_type == "screenshot":
            res = adapter.screenshot(session_id=session_id, contract=contract)
            if res.ok:
                extracted["screenshot"] = res.data
        elif action_type == "download":
            res = adapter.download(
                url=str(action.get("url") or start_url),
                output_path=str(action.get("output_path") or ""),
                session_id=session_id,
                contract=contract,
            )
            if not res.ok:
                self._audit(
                    "browser_policy_blocked", {"provider": "camofox", "reason": res.policy_denial_code or res.error}
                )
                raise _CamofoxActionFailed(res.policy_denial_code or res.error or "camofox_download_failed")
            extracted.update(res.data)

    def _run_session(
        self, adapter: Any, contract: Any, start_url: str, actions: list
    ) -> tuple[BackendResult | None, dict, int, str | None]:
        """Navigate and run the actions; returns (failure or None, extracted data, actions executed, session)."""
        extracted: dict = {}
        actions_executed = 1
        session_id: str | None = None
        try:
            session_id = adapter.create_session(contract=contract)
            nav = adapter.navigate(url=start_url, session_id=session_id, contract=contract)
            if not nav.ok:
                CAMOFOX_OBSERVABILITY["calls"] += 1
                CAMOFOX_OBSERVABILITY["last_failure_class"] = nav.policy_denial_code or "navigate_failed"
                self._audit(
                    "browser_policy_blocked", {"provider": "camofox", "reason": nav.policy_denial_code or nav.error}
                )
                failure = (-1, "", nav.policy_denial_code or nav.error or "camofox_navigate_failed")
                return failure, extracted, actions_executed, session_id
            for action in actions:
                if actions_executed > contract.max_actions:
                    break
                self._run_action(
                    adapter, action, start_url=start_url, session_id=session_id, contract=contract, extracted=extracted
                )
                actions_executed += 1
        except _CamofoxActionFailed as failed:
            return (-1, "", failed.reason), extracted, actions_executed, session_id
        finally:
            if session_id and not contract.persist_session:
                adapter.close_session(session_id=session_id)
        return None, extracted, actions_executed, session_id
