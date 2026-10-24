"""Hermetic auth environment for research-training tests.

The file-managed agent token is a production service credential. With it
configured, the test app's user-JWT admin login is not the authentication
path these API tests exercise, and the requests fail unauthenticated. Tests
must run self-contained regardless of ambient operator secrets.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def hermetic_agent_token_env(app, monkeypatch):
    monkeypatch.delenv("AGENT_TOKEN_FILE", raising=False)
    monkeypatch.delenv("AGENT_TOKEN_PERSISTENCE", raising=False)
    app.config.pop("AGENT_TOKEN_FILE", None)
    app.config.pop("AGENT_TOKEN_PERSISTENCE", None)
