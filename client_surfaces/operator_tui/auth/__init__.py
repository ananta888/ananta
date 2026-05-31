"""Carbonyl OIDC auth subsystem for the Operator TUI.

This is a separate auth subsystem from the Device Flow (oidc_device_flow.py).
It supports authorization_code_pkce flows with a loopback callback listener.
"""
from __future__ import annotations

from client_surfaces.operator_tui.auth.oidc_models import (
    OidcAuthRequest,
    OidcAuthResult,
    OidcProviderConfig,
)
from client_surfaces.operator_tui.auth.oidc_auth_controller import OidcAuthController
from client_surfaces.operator_tui.auth.loopback_callback_server import LoopbackCallbackServer
from client_surfaces.operator_tui.auth.oidc_audit import OidcAuditEvent, OidcAuditLog
from client_surfaces.operator_tui.auth.oidc_browser_session_controller import (
    OidcBrowserSession,
    OidcBrowserSessionController,
)

__all__ = [
    "OidcProviderConfig",
    "OidcAuthRequest",
    "OidcAuthResult",
    "OidcAuthController",
    "OidcBrowserSession",
    "OidcBrowserSessionController",
    "LoopbackCallbackServer",
    "OidcAuditEvent",
    "OidcAuditLog",
]
