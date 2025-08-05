import json
import controller.controller as cc


def test_update_active_agent(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"agents": {"A": {}, "B": {}}, "active_agent": "A"}))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg))
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)
    with cc.app.test_client() as client:
        resp = client.post('/config/active_agent', json={"active_agent": "B"})
        assert resp.status_code == 200
        saved = json.loads(cfg.read_text())
        assert saved["active_agent"] == "B"
