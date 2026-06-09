from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests

from agent.common.audit import log_audit
from agent.services.browser_policy_service import get_browser_policy_service
from agent.services.browser_task_contract import BrowserTaskContract

# Alle browser-relevanten Audit-Eventcodes
_AUDIT_NAVIGATE = "browser_camofox_navigate"
_AUDIT_DOWNLOAD = "browser_camofox_download"
_AUDIT_POLICY_DENIED = "browser_camofox_policy_denied"
_AUDIT_SESSION_CLOSE = "browser_camofox_session_close"
_AUDIT_HEALTH_FAIL = "browser_camofox_health_fail"


@dataclass(frozen=True)
class CamofoxActionResult:
    ok: bool
    action: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    policy_denial_code: str | None = None


class BrowserCamofoxAdapter:
    """REST-Adapter für den Camoufox-Browser-Server (Anti-Bot-fähig, Firefox-basiert).

    Sicherheitsarchitektur: BrowserPolicyService liegt VOR jedem REST-Call.
    Der Adapter darf niemals direkt aus einem Agent-Prompt heraus erreichbar sein.
    """

    def __init__(self, base_url: str = "http://localhost:9377", timeout_seconds: int = 30):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def health_check(self) -> dict[str, Any]:
        """Prüft ob der Camoufox-Server erreichbar ist."""
        try:
            resp = requests.get(f"{self._base_url}/health", timeout=5)
            if resp.status_code == 200:
                return {"healthy": True, "status_code": 200, "backend": "camofox"}
            log_audit(_AUDIT_HEALTH_FAIL, {"status_code": resp.status_code, "base_url": self._base_url})
            return {"healthy": False, "status_code": resp.status_code, "backend": "camofox"}
        except Exception as exc:
            log_audit(_AUDIT_HEALTH_FAIL, {"error": str(exc), "base_url": self._base_url})
            return {"healthy": False, "error": str(exc), "backend": "camofox"}

    def create_session(self, *, contract: BrowserTaskContract) -> str:
        """Eröffnet eine neue Browser-Session. Gibt session_id zurück."""
        payload: dict[str, Any] = {
            "persist": contract.persist_session,
            "timeout_seconds": contract.timeout_seconds,
        }
        resp = self._post("/sessions", payload)
        session_id: str = resp.get("session_id") or str(uuid.uuid4())
        return session_id

    def navigate(
        self,
        *,
        url: str,
        session_id: str,
        contract: BrowserTaskContract,
    ) -> CamofoxActionResult:
        """Navigiert zur URL. Prüft Domain- und Blocked-Host-Policy."""
        policy = get_browser_policy_service()

        blocked = policy.enforce_blocked_hosts(url=url, contract=contract)
        if not blocked.allow:
            log_audit(_AUDIT_POLICY_DENIED, {"reason": blocked.reason_code, "url": url, "session_id": session_id})
            return CamofoxActionResult(False, "navigate", session_id, policy_denial_code=blocked.reason_code)

        domain = policy.enforce_domain(url=url, contract=contract)
        if not domain.allow:
            log_audit(_AUDIT_POLICY_DENIED, {"reason": domain.reason_code, "url": url, "session_id": session_id})
            return CamofoxActionResult(False, "navigate", session_id, policy_denial_code=domain.reason_code)

        log_audit(_AUDIT_NAVIGATE, {"url": url, "session_id": session_id})
        try:
            data = self._post(f"/sessions/{session_id}/navigate", {"url": url})
            return CamofoxActionResult(True, "navigate", session_id, data=data)
        except Exception as exc:
            return CamofoxActionResult(False, "navigate", session_id, error=str(exc))

    def read_page(
        self,
        *,
        session_id: str,
        contract: BrowserTaskContract,
    ) -> CamofoxActionResult:
        """Liest Seiteninhalt (Text/HTML) der aktuellen Session-Seite."""
        try:
            data = self._get(f"/sessions/{session_id}/page")
            return CamofoxActionResult(True, "read_page", session_id, data=data)
        except Exception as exc:
            return CamofoxActionResult(False, "read_page", session_id, error=str(exc))

    def click(
        self,
        *,
        selector: str,
        session_id: str,
        contract: BrowserTaskContract,
    ) -> CamofoxActionResult:
        """Klickt ein Element via CSS-Selektor."""
        try:
            data = self._post(f"/sessions/{session_id}/click", {"selector": selector})
            return CamofoxActionResult(True, "click", session_id, data=data)
        except Exception as exc:
            return CamofoxActionResult(False, "click", session_id, error=str(exc))

    def type_text(
        self,
        *,
        selector: str,
        text: str,
        session_id: str,
        contract: BrowserTaskContract,
    ) -> CamofoxActionResult:
        """Tippt Text in ein Eingabefeld."""
        try:
            data = self._post(f"/sessions/{session_id}/type", {"selector": selector, "text": text})
            return CamofoxActionResult(True, "type_text", session_id, data=data)
        except Exception as exc:
            return CamofoxActionResult(False, "type_text", session_id, error=str(exc))

    def screenshot(
        self,
        *,
        session_id: str,
        contract: BrowserTaskContract,
    ) -> CamofoxActionResult:
        """Macht einen Screenshot, wenn die Policy es erlaubt."""
        if contract.screenshot_policy == "none":
            return CamofoxActionResult(
                False, "screenshot", session_id,
                policy_denial_code="browser_policy_screenshot_denied",
            )
        try:
            data = self._get(f"/sessions/{session_id}/screenshot")
            return CamofoxActionResult(True, "screenshot", session_id, data=data)
        except Exception as exc:
            return CamofoxActionResult(False, "screenshot", session_id, error=str(exc))

    def download(
        self,
        *,
        url: str,
        output_path: str,
        session_id: str,
        contract: BrowserTaskContract,
    ) -> CamofoxActionResult:
        """Lädt eine Datei herunter. Prüft Download-Policy und Blocked-Hosts."""
        policy = get_browser_policy_service()

        blocked = policy.enforce_blocked_hosts(url=url, contract=contract)
        if not blocked.allow:
            log_audit(_AUDIT_POLICY_DENIED, {"reason": blocked.reason_code, "url": url, "action": "download"})
            return CamofoxActionResult(False, "download", session_id, policy_denial_code=blocked.reason_code)

        download_decision = policy.enforce_download_policy(
            download_url=url, output_path=output_path, contract=contract
        )
        if not download_decision.allow:
            log_audit(_AUDIT_POLICY_DENIED, {"reason": download_decision.reason_code, "url": url, "action": "download"})
            return CamofoxActionResult(False, "download", session_id, policy_denial_code=download_decision.reason_code)

        log_audit(_AUDIT_DOWNLOAD, {"url": url, "output_path": output_path, "session_id": session_id})
        try:
            data = self._post(
                f"/sessions/{session_id}/download",
                {"url": url, "output_path": output_path},
            )
            return CamofoxActionResult(True, "download", session_id, data=data)
        except Exception as exc:
            return CamofoxActionResult(False, "download", session_id, error=str(exc))

    def close_session(self, *, session_id: str) -> CamofoxActionResult:
        """Schließt eine Browser-Session und gibt Ressourcen frei."""
        log_audit(_AUDIT_SESSION_CLOSE, {"session_id": session_id})
        try:
            data = self._delete(f"/sessions/{session_id}")
            return CamofoxActionResult(True, "close_session", session_id, data=data)
        except Exception as exc:
            return CamofoxActionResult(False, "close_session", session_id, error=str(exc))

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        resp = requests.post(
            f"{self._base_url}{path}",
            json=payload,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _get(self, path: str) -> dict[str, Any]:
        resp = requests.get(f"{self._base_url}{path}", timeout=self._timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def _delete(self, path: str) -> dict[str, Any]:
        resp = requests.delete(f"{self._base_url}{path}", timeout=self._timeout)
        resp.raise_for_status()
        return resp.json() if resp.content else {}


def build_camofox_adapter(browser_cfg: dict[str, Any]) -> BrowserCamofoxAdapter:
    """Erstellt einen konfigurierten Adapter aus dem browser-Config-Block."""
    base_url = str(browser_cfg.get("camofox_url") or "http://localhost:9377").rstrip("/")
    timeout = int(browser_cfg.get("timeout_seconds") or 30)
    return BrowserCamofoxAdapter(base_url=base_url, timeout_seconds=timeout)
