"""Carbonyl browser mode controller for the Operator TUI center viewport.

Manages two OIDC-capable Carbonyl browser modes:

  A) Real browser session (``open_oidc_provider_session``):
     Carbonyl navigates to the provider URL; cookies/session storage stay in
     the isolated Carbonyl profile for that session.

  B) Ananta-owned OIDC callback (``start_oidc_login`` / ``complete_oidc_login``):
     Ananta starts a loopback listener, validates state/nonce, exchanges the
     authorization code, and owns tokens outside the browser document.

Command namespace: ``center.browser.oidc.*``

Security invariants:
- Raw tokens are NEVER placed in generated HTML, URLs, temp files or logs.
- Loopback listener is started BEFORE Carbonyl opens the authorization URL.
- Provider rejections are surfaced as compatibility failures, not TUI crashes.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from client_surfaces.operator_tui.auth.oidc_models import (
    OidcAuthRequest,
    OidcAuthResult,
    OidcProviderConfig,
)
from client_surfaces.operator_tui.auth.oidc_auth_controller import OidcAuthController
from client_surfaces.operator_tui.auth.loopback_callback_server import LoopbackCallbackServer
from client_surfaces.operator_tui.auth.oidc_audit import (
    OidcAuditEvent,
    OidcAuditLog,
    EVENT_LOGIN_START,
    EVENT_CALLBACK_RECEIVED,
    EVENT_STATE_VALIDATED,
    EVENT_TOKEN_EXCHANGE_SUCCESS,
    EVENT_TOKEN_EXCHANGE_FAILED,
    EVENT_PROVIDER_REJECTED,
    EVENT_LOGOUT,
    EVENT_PROFILE_CLEANUP,
    MODE_ANANTA_OWNED,
    MODE_REAL_BROWSER,
    PROFILE_EPHEMERAL,
    PROFILE_NAMED,
)
from client_surfaces.operator_tui.visual.browser.carbonyl_profile_manager import (
    CarbonylProfile,
    CarbonylProfileManager,
)

# Login state values
LOGIN_UNKNOWN = "unknown"
LOGIN_IN_PROGRESS = "in_progress"
LOGIN_SUCCESS = "logged_in"
LOGIN_FAILED = "failed"


@dataclass
class BrowserSessionState:
    """Current state of the Carbonyl browser session."""
    provider_id: str = ""
    profile_mode: str = PROFILE_EPHEMERAL  # "ephemeral" | "named"
    login_state: str = LOGIN_UNKNOWN  # "unknown" | "in_progress" | "logged_in" | "failed"
    mode: str = MODE_ANANTA_OWNED  # "ananta_owned_callback" | "real_browser_session"
    error: str = ""
    started_at: float = field(default_factory=time.time)


class BrowserModeController:
    """Controls Carbonyl browser sessions for OIDC login in the Operator TUI.

    This controller is stateful — one instance per TUI session.  It tracks the
    active browser process, current profile, and OIDC request state.
    """

    def __init__(
        self,
        profile_manager: Optional[CarbonylProfileManager] = None,
        audit_log: Optional[OidcAuditLog] = None,
        carbonyl_binary: str = "carbonyl",
    ) -> None:
        self._profile_manager = profile_manager or CarbonylProfileManager()
        self._audit = audit_log or OidcAuditLog()
        self._carbonyl_binary = carbonyl_binary
        self._session_state: BrowserSessionState = BrowserSessionState()
        self._active_profile: Optional[CarbonylProfile] = None
        self._active_request: Optional[OidcAuthRequest] = None
        self._active_provider: Optional[OidcProviderConfig] = None
        self._loopback: Optional[LoopbackCallbackServer] = None
        self._browser_proc: Optional[subprocess.Popen] = None  # type: ignore[type-arg]
        self._auth_controller = OidcAuthController()

    # ---------------------------------------------------------------------------
    # oidc-004: Real browser session mode
    # Command: center.browser.oidc.open_provider_session
    # ---------------------------------------------------------------------------

    def open_oidc_provider_session(
        self,
        provider: OidcProviderConfig,
        profile: CarbonylProfile,
    ) -> BrowserSessionState:
        """Launch Carbonyl as a real browser session for the given provider.

        Carbonyl navigates directly to the provider's issuer URL.  Cookies and
        session storage stay inside the isolated profile directory.

        Args:
            provider: OIDC provider configuration.
            profile: The Carbonyl profile to use for this session.

        Returns:
            Updated ``BrowserSessionState`` reflecting the new session.
        """
        self._active_profile = profile
        self._active_provider = provider
        profile_mode = PROFILE_EPHEMERAL if profile.ephemeral else PROFILE_NAMED

        self._session_state = BrowserSessionState(
            provider_id=provider.provider_id,
            profile_mode=profile_mode,
            login_state=LOGIN_IN_PROGRESS,
            mode=MODE_REAL_BROWSER,
        )

        self._audit.emit(OidcAuditEvent(
            event_type=EVENT_LOGIN_START,
            provider_id=provider.provider_id,
            mode=MODE_REAL_BROWSER,
            profile_mode=profile_mode,
            error_category="",
        ))

        provider_url = provider.issuer.rstrip("/") + "/"
        try:
            self._browser_proc = self._launch_carbonyl(
                url=provider_url,
                user_data_dir=str(profile.profile_dir),
            )
        except FileNotFoundError:
            # Carbonyl binary not available — show as compatibility failure
            error = "carbonyl_binary_not_found"
            self._session_state.login_state = LOGIN_FAILED
            self._session_state.error = error
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_PROVIDER_REJECTED,
                provider_id=provider.provider_id,
                mode=MODE_REAL_BROWSER,
                profile_mode=profile_mode,
                error_category=error,
            ))
        except Exception as exc:
            # Generic launch failure — never crash the TUI
            error = f"browser_launch_failed"
            self._session_state.login_state = LOGIN_FAILED
            self._session_state.error = error
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_PROVIDER_REJECTED,
                provider_id=provider.provider_id,
                mode=MODE_REAL_BROWSER,
                profile_mode=profile_mode,
                error_category=error,
            ))

        return self._session_state

    # ---------------------------------------------------------------------------
    # oidc-005: Ananta-owned OIDC callback mode
    # ---------------------------------------------------------------------------

    def start_oidc_login(
        self,
        provider: OidcProviderConfig,
        timeout_seconds: float = 180.0,
        ephemeral: bool = True,
    ) -> str:
        """Start an Ananta-owned OIDC login and return the authorization URL.

        Starts the loopback listener BEFORE returning the URL, so no race
        condition exists between starting Carbonyl and the callback arriving.

        Args:
            provider: OIDC provider configuration.
            timeout_seconds: How long to wait for the callback.
            ephemeral: Whether to use an ephemeral Carbonyl profile.

        Returns:
            The authorization URL to open in Carbonyl.
        """
        profile_mode = PROFILE_EPHEMERAL if ephemeral else PROFILE_NAMED

        self._audit.emit(OidcAuditEvent(
            event_type=EVENT_LOGIN_START,
            provider_id=provider.provider_id,
            mode=MODE_ANANTA_OWNED,
            profile_mode=profile_mode,
            error_category="",
        ))

        # 1. Start loopback listener to get a redirect_uri
        loopback = LoopbackCallbackServer()
        redirect_uri = loopback.start(timeout_seconds=timeout_seconds)
        self._loopback = loopback

        # 2. Create the authorization request (needs redirect_uri)
        request = self._auth_controller.create_authorization_request(
            provider=provider,
            redirect_uri=redirect_uri,
        )
        self._active_request = request
        self._active_provider = provider

        # 3. Create an isolated Carbonyl profile
        profile = self._profile_manager.create_profile(
            provider_id=provider.provider_id,
            ephemeral=ephemeral,
        )
        self._active_profile = profile

        self._session_state = BrowserSessionState(
            provider_id=provider.provider_id,
            profile_mode=profile_mode,
            login_state=LOGIN_IN_PROGRESS,
            mode=MODE_ANANTA_OWNED,
        )

        return request.authorization_url

    def complete_oidc_login(self, callback_url: str) -> OidcAuthResult:
        """Validate callback and exchange authorization code for tokens.

        This method is called after the browser receives the callback.
        When called from the loopback server path, ``wait_and_complete``
        is preferred.

        Args:
            callback_url: The full callback URL including query parameters.

        Returns:
            ``OidcAuthResult`` with tokens on success.
        """
        if self._active_request is None or self._active_provider is None:
            return OidcAuthResult(
                ok=False,
                error="no_active_oidc_request",
            )

        profile_mode = (
            PROFILE_EPHEMERAL
            if (self._active_profile and self._active_profile.ephemeral)
            else PROFILE_NAMED
        )

        self._audit.emit(OidcAuditEvent(
            event_type=EVENT_CALLBACK_RECEIVED,
            provider_id=self._active_request.provider_id,
            mode=MODE_ANANTA_OWNED,
            profile_mode=profile_mode,
            error_category="",
        ))

        try:
            result = self._auth_controller.complete_callback(
                callback_url=callback_url,
                request=self._active_request,
                provider=self._active_provider,
            )
        except ValueError as exc:
            error_category = "invalid_state"
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_TOKEN_EXCHANGE_FAILED,
                provider_id=self._active_request.provider_id,
                mode=MODE_ANANTA_OWNED,
                profile_mode=profile_mode,
                error_category=error_category,
            ))
            self._session_state.login_state = LOGIN_FAILED
            self._session_state.error = error_category
            return OidcAuthResult(
                ok=False,
                error=error_category,
                provider_id=self._active_request.provider_id,
            )
        except Exception:
            error_category = "token_exchange_failed"
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_TOKEN_EXCHANGE_FAILED,
                provider_id=self._active_request.provider_id,
                mode=MODE_ANANTA_OWNED,
                profile_mode=profile_mode,
                error_category=error_category,
            ))
            self._session_state.login_state = LOGIN_FAILED
            self._session_state.error = error_category
            return OidcAuthResult(
                ok=False,
                error=error_category,
                provider_id=self._active_request.provider_id,
            )

        if result.ok:
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_STATE_VALIDATED,
                provider_id=result.provider_id,
                mode=MODE_ANANTA_OWNED,
                profile_mode=profile_mode,
                error_category="",
            ))
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_TOKEN_EXCHANGE_SUCCESS,
                provider_id=result.provider_id,
                mode=MODE_ANANTA_OWNED,
                profile_mode=profile_mode,
                error_category="",
            ))
            self._session_state.login_state = LOGIN_SUCCESS
            self._session_state.error = ""
        else:
            error_category = result.error or "token_exchange_failed"
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_TOKEN_EXCHANGE_FAILED,
                provider_id=result.provider_id,
                mode=MODE_ANANTA_OWNED,
                profile_mode=profile_mode,
                error_category=error_category,
            ))
            self._session_state.login_state = LOGIN_FAILED
            self._session_state.error = error_category

        return result

    def wait_and_complete(self) -> OidcAuthResult:
        """Block until the loopback callback arrives, then complete the login.

        This is the normal path for Ananta-owned callback mode.

        Returns:
            ``OidcAuthResult`` with tokens on success or an error result.
        """
        if self._loopback is None:
            return OidcAuthResult(ok=False, error="no_loopback_server")

        callback_data = self._loopback.wait_for_callback()
        if "error" in callback_data:
            error_category = callback_data["error"]
            provider_id = (
                self._active_request.provider_id
                if self._active_request
                else ""
            )
            profile_mode = (
                PROFILE_EPHEMERAL
                if (self._active_profile and self._active_profile.ephemeral)
                else PROFILE_NAMED
            )
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_CALLBACK_RECEIVED,
                provider_id=provider_id,
                mode=MODE_ANANTA_OWNED,
                profile_mode=profile_mode,
                error_category=error_category,
            ))
            self._session_state.login_state = LOGIN_FAILED
            self._session_state.error = error_category
            return OidcAuthResult(
                ok=False,
                error=error_category,
                provider_id=provider_id,
            )

        # Build the full callback URL from the loopback result
        redirect_uri = self._loopback.redirect_uri
        code = callback_data.get("code", "")
        state = callback_data.get("state", "")
        callback_url = f"{redirect_uri}?code={code}&state={state}"

        return self.complete_oidc_login(callback_url=callback_url)

    def logout(self) -> None:
        """Invalidate the current session and optionally clean up the profile."""
        provider_id = self._session_state.provider_id
        profile_mode = self._session_state.profile_mode
        mode = self._session_state.mode

        self._audit.emit(OidcAuditEvent(
            event_type=EVENT_LOGOUT,
            provider_id=provider_id,
            mode=mode,
            profile_mode=profile_mode,
            error_category="",
        ))

        if self._active_profile:
            self._audit.emit(OidcAuditEvent(
                event_type=EVENT_PROFILE_CLEANUP,
                provider_id=provider_id,
                mode=mode,
                profile_mode=profile_mode,
                error_category="",
            ))
            self._profile_manager.cleanup_profile(self._active_profile)
            self._active_profile = None

        if self._browser_proc:
            try:
                self._browser_proc.terminate()
            except Exception:
                pass
            self._browser_proc = None

        if self._loopback:
            try:
                self._loopback.stop()
            except Exception:
                pass
            self._loopback = None

        self._active_request = None
        self._active_provider = None
        self._session_state = BrowserSessionState()

    @property
    def session_state(self) -> BrowserSessionState:
        """Current browser session state (read-only snapshot)."""
        return self._session_state

    @property
    def audit_log(self) -> OidcAuditLog:
        """The audit log attached to this controller."""
        return self._audit

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _launch_carbonyl(self, url: str, user_data_dir: str) -> subprocess.Popen:  # type: ignore[type-arg]
        """Launch the Carbonyl browser subprocess.

        Args:
            url: The URL to open.
            user_data_dir: Path to the isolated user-data-dir.

        Returns:
            The running ``subprocess.Popen`` object.

        Raises:
            FileNotFoundError: If the Carbonyl binary is not found.
        """
        cmd = [
            self._carbonyl_binary,
            f"--user-data-dir={user_data_dir}",
            url,
        ]
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @staticmethod
    def _build_success_page(username: str = "", provider_id: str = "") -> str:
        """Build a minimal local success HTML page.

        Tokens are NEVER embedded in this page.

        Args:
            username: Display name (safe, not a token).
            provider_id: The provider that was used.

        Returns:
            HTML string for the success page.
        """
        safe_username = username.replace("<", "&lt;").replace(">", "&gt;") if username else "user"
        safe_provider = provider_id.replace("<", "&lt;").replace(">", "&gt;") if provider_id else "provider"
        return f"""\
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Ananta – Login successful</title></head>
<body>
<h2>Login successful</h2>
<p>Logged in as <strong>{safe_username}</strong> via {safe_provider}.</p>
<p>Ananta TUI is now authenticated. You may close this tab.</p>
</body>
</html>
"""
