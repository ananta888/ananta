import json

from agent.cli.commands import text_quality


class _Client:
    calls = []

    def post(self, path, *, json):
        self.calls.append(("POST", path, json))
        return {"status": "success"}

    def get(self, path):
        self.calls.append(("GET", path, None))
        return {"status": "success", "data": {"items": []}}


def test_cli_uses_hub_api_for_evaluate_and_extract(monkeypatch, capsys):
    client = _Client()
    monkeypatch.setattr(text_quality, "AnantaApiClient", lambda: client)
    assert text_quality.dispatch(["evaluate", "Konkreter Text"]) == 0
    assert text_quality.dispatch(["criteria-extract", "Floskelhafter Text"]) == 0
    assert client.calls[0][1] == "/api/text-quality/evaluate"
    assert client.calls[1][1] == "/api/text-quality/criteria/extract"
    assert json.loads(capsys.readouterr().out.splitlines()[0])["status"] == "success"


def test_cli_review_actions_are_stable(monkeypatch):
    client = _Client()
    monkeypatch.setattr(text_quality, "AnantaApiClient", lambda: client)
    assert text_quality.dispatch(["criteria-archive", "criteria-1"]) == 0
    assert client.calls[-1][1] == "/api/text-quality/criteria/criteria-1/archive"
