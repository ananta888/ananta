import yaml
import controller.controller as cc


def test_update_api_endpoints(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("api_endpoints: []\n")
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg))
    cc.config_manager = cc.ConfigManager(cfg)
    with cc.app.test_client() as client:
        resp = client.post('/config/api_endpoints', json={"api_endpoints": [{"type": "x", "url": "y", "models": ["m1", "m2"]}]})
        assert resp.status_code == 200
        saved = yaml.safe_load(cfg.read_text())
        assert saved["api_endpoints"] == [{"type": "x", "url": "y", "models": ["m1", "m2"]}]
