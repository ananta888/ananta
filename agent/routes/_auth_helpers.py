"""SPLIT-037: Shared helpers for agent.routes.auth sub-modules.

Defines the ``_repos``/``_log``/``_is_local_test_request``/
``_ensure_test_endpoint_enabled`` helpers plus the ``MFA_WARN_LAST``
de-duplication cache. The :mod:`agent.routes.auth` shim re-exports
these names so that tests patching ``agent.routes.auth._log`` win
the monkey-patch race even when consumers in other sub-modules
look the symbols up at call time.
"""
from __future__ import annotations

from ipaddress import ip_address

from flask import request

from agent.common.errors import api_response
from agent.config import settings
from agent.services.repository_registry import get_repository_registry
from agent.services.service_registry import get_core_services

# Reduziert MFA-Log-Noise: speichert Zeitstempel des letzten WARN-Logs pro User/IP
MFA_WARN_LAST: dict = {}


def _repos():
    return get_repository_registry()


def _log():
    return get_core_services().log_service.bind(__name__)


def _is_local_test_request() -> bool:
    remote = request.remote_addr or ""
    try:
        ip = ip_address(remote)
        return ip.is_loopback or ip.is_private
    except ValueError:
        return False


def _ensure_test_endpoint_enabled():
    if not settings.auth_test_endpoints_enabled:
        return api_response(status="error", message="Not found", code=404)
    if not _is_local_test_request():
        return api_response(status="error", message="forbidden", code=403)
    return None
