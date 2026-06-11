"""SPLIT-037: Auth blueprint shim (1010 -> ~50 lines).

The original 1010-line ``auth.py`` is decomposed into five
single-responsibility sub-modules:

  * :mod:`agent.routes._auth_helpers`    — service-container accessors,
    local-test detection, ``MFA_WARN_LAST`` de-dupe cache.
  * :mod:`agent.routes._auth_password`   — password complexity, rate
    limiting, history, lockout policy helpers (no Flask routes).
  * :mod:`agent.routes._auth_session_routes` — /login, /refresh-token,
    /me, /change-password.
  * :mod:`agent.routes._auth_mfa_routes`     — /mfa/setup, /mfa/verify,
    /mfa/disable.
  * :mod:`agent.routes._auth_users_routes`   — /users, /users/<name>,
    /users/<name>/reset-password, /users/<name>/role (admin).
  * :mod:`agent.routes._auth_test_routes`    — /test/* auth helpers,
    gated by ``auth_test_endpoints_enabled``.

This shim is the **single owner** of the ``_log``/``_repos``/
``_is_local_test_request``/``_ensure_test_endpoint_enabled`` names
and re-exports them. The :func:`_auth_helpers` module only defines
the implementation; consumers (and external tests that patch
``agent.routes.auth._log``) must look the symbols up via this
module's namespace at call time so monkey-patches win the
attribute-resolution race across module boundaries.
"""
from __future__ import annotations

from flask import Blueprint

from agent.routes import _auth_helpers as _helpers

# Public shim symbols (the original auth.py kept these at module
# scope; tests and other modules import them from this path). The
# helper module owns the actual implementation.
_repos = _helpers._repos
_log = _helpers._log
_is_local_test_request = _helpers._is_local_test_request
_ensure_test_endpoint_enabled = _helpers._ensure_test_endpoint_enabled
MFA_WARN_LAST = _helpers.MFA_WARN_LAST

from agent.routes._auth_mfa_routes import register_routes as _register_mfa
from agent.routes._auth_session_routes import register_routes as _register_session
from agent.routes._auth_test_routes import register_routes as _register_test
from agent.routes._auth_users_routes import register_routes as _register_users

auth_bp = Blueprint("auth", __name__)

# Attach every sub-module's routes onto the shared blueprint.
_register_session(auth_bp)
_register_mfa(auth_bp)
_register_users(auth_bp)
_register_test(auth_bp)
