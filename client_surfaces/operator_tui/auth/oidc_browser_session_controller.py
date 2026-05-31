"""OIDC browser session coordinator for Carbonyl flows."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from client_surfaces.operator_tui.auth.loopback_callback_server import LoopbackCallbackServer
from client_surfaces.operator_tui.auth.oidc_auth_controller import OidcAuthController
from client_surfaces.operator_tui.auth.oidc_models import OidcAuthRequest, OidcAuthResult, OidcProviderConfig
from client_surfaces.operator_tui.visual.browser.browser_mode_controller import BrowserModeController
from client_surfaces.operator_tui.visual.browser.carbonyl_profile_manager import CarbonylProfile


@dataclass
class OidcBrowserSession:
    """In-flight Ananta-owned callback session."""

    provider: OidcProviderConfig
    request: OidcAuthRequest
    redirect_uri: str
    profile: CarbonylProfile | None
    status: str = "started"


class OidcBrowserSessionController:
    """Wire AuthController, loopback listener, and Carbonyl browser mode.

    This coordinator owns only the OIDC browser-session flow. Token exchange
    remains in ``OidcAuthController`` and Carbonyl lifecycle remains in
    ``BrowserModeController``.
    """

    def __init__(
        self,
        *,
        auth_controller: OidcAuthController | None = None,
        callback_server: LoopbackCallbackServer | None = None,
        browser_controller: BrowserModeController | None = None,
    ) -> None:
        self._auth = auth_controller or OidcAuthController()
        self._callback = callback_server or LoopbackCallbackServer()
        self._browser = browser_controller or BrowserModeController()
        self._active: OidcBrowserSession | None = None

    @property
    def active_session(self) -> OidcBrowserSession | None:
        return self._active

    def start_ananta_owned_callback(
        self,
        provider: OidcProviderConfig,
        *,
        cols: int,
        rows: int,
        callback_timeout_seconds: float = 180.0,
    ) -> OidcBrowserSession:
        """Start loopback callback and open the provider URL in Carbonyl."""
        redirect_uri = self._callback.start(timeout_seconds=callback_timeout_seconds)
        request = self._auth.create_authorization_request(provider, redirect_uri)
        profile = self._browser.open_oidc_provider_session(
            request.authorization_url,
            provider_id=provider.provider_id,
            cols=cols,
            rows=rows,
            ephemeral_profile=True,
        )
        session = OidcBrowserSession(
            provider=provider,
            request=request,
            redirect_uri=redirect_uri,
            profile=profile,
            status="browser_active" if profile is not None else "browser_unavailable",
        )
        self._active = session
        return session

    def complete_callback(self, callback_payload: dict[str, str] | None = None) -> OidcAuthResult:
        """Complete the active callback flow.

        ``callback_payload`` is optional for tests or external integrations. If
        omitted, the controller waits for the loopback server.
        """
        session = self._require_active()
        payload = callback_payload if callback_payload is not None else self._callback.wait_for_callback()
        callback_url = _callback_url_from_payload(session.redirect_uri, payload)
        result = self._auth.complete_callback(callback_url, session.request, session.provider)
        session.status = "complete" if result.ok else "failed"
        return result

    def stop(self) -> None:
        self._callback.stop()
        self._browser.exit_browser_mode()
        if self._active is not None:
            self._active.status = "stopped"

    def build_realtime_identity(self, result: OidcAuthResult) -> dict[str, str]:
        return self._auth.build_realtime_identity(result)

    def _require_active(self) -> OidcBrowserSession:
        if self._active is None:
            raise RuntimeError("No active OIDC browser session.")
        return self._active


def _callback_url_from_payload(redirect_uri: str, payload: dict[str, str]) -> str:
    if "error" in payload:
        fields = {
            "error": payload.get("error", ""),
            "error_description": payload.get("error_description", ""),
            "state": payload.get("state", ""),
        }
    else:
        fields = {
            "code": payload.get("code", ""),
            "state": payload.get("state", ""),
        }
    return f"{redirect_uri}?{urlencode(fields)}"
