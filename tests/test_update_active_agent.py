import yaml
import controller.controller as cc


def test_update_active_agent(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("agents:\n  A: {}\n  B: {}\nactive_agent: A\napi_endpoints: []\nprompt_templates: {}\n")
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg))
    cc.config_manager = cc.ConfigManager(cfg)
    with cc.app.test_client() as client:
        resp = client.post('/config/active_agent', json={"active_agent": "B"})
        assert resp.status_code == 200
        saved = yaml.safe_load(cfg.read_text())
        assert saved["active_agent"] == "B"
