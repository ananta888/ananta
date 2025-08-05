import json
import controller.controller as cc

def test_update_api_endpoints(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"api_endpoints": []}))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg))
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)
    with cc.app.test_client() as client:
        resp = client.post('/config/api_endpoints', json={"api_endpoints": [{"type": "x", "url": "y", "models": ["m1", "m2"]}]})
        assert resp.status_code == 200
        saved = json.loads(cfg.read_text())
        assert saved["api_endpoints"] == [{"type": "x", "url": "y", "models": ["m1", "m2"]}]
