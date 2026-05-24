from __future__ import annotations

from types import SimpleNamespace

from agent.cli.commands import ssh as ssh_cli


class _Resp:
    def __init__(self, headers=None, payload=None):
        self.headers = headers or {}
        self._payload = payload or {}

    def json(self):
        return self._payload


class _ClientNoLocation:
    def __init__(self, base_url: str, token: str | None):
        self.base_url = base_url
        self.token = token

    def get(self, path: str, allow_redirects: bool = False):
        return _Resp(headers={})


class _ClientOkLocation:
    def __init__(self, base_url: str, token: str | None):
        self.base_url = base_url
        self.token = token

    def get(self, path: str, allow_redirects: bool = False):
        return _Resp(headers={"Location": "http://issuer/auth"})


def test_cmd_login_requires_redirect_location(monkeypatch):
    import agent.cli.api_client as api_client

    monkeypatch.setattr(api_client, "AnantaApiClient", _ClientNoLocation)
    rc = ssh_cli._cmd_login(SimpleNamespace(), "http://localhost:5000", None)
    assert rc == 1


def test_cmd_login_success_on_redirect(monkeypatch):
    import agent.cli.api_client as api_client

    monkeypatch.setattr(api_client, "AnantaApiClient", _ClientOkLocation)
    rc = ssh_cli._cmd_login(SimpleNamespace(), "http://localhost:5000", None)
    assert rc == 0


def test_cmd_connect_hub_requires_reason():
    args = SimpleNamespace(
        target_type="hub",
        target_id="hub",
        workspace=None,
        goal_id=None,
        task_id=None,
        reason="",
    )
    rc = ssh_cli._cmd_connect(args, "http://localhost:5000", None)
    assert rc == 1
