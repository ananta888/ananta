import json
import pytest
import controller.controller as cc

@pytest.fixture(autouse=True)
def stub_ai_agent(monkeypatch, tmp_path):
    """Stub ai-agent HTTP calls to use local config file."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({}))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg))

    def fake_http_get(url, retries=1, delay=0):
        if url.endswith("/config"):
            with open(cc.CONFIG_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        raise RuntimeError("unexpected url")

    monkeypatch.setattr(cc, "_http_get", fake_http_get)
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)
    yield
