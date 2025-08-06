import json
import importlib


def setup_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cc = importlib.import_module("controller.controller")
    importlib.reload(cc)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(
        json.dumps({
            "agents": {"default": {}},
            "active_agent": "default",
            "tasks": [],
            "api_endpoints": [],
            "prompt_templates": {},
        })
    )
    (tmp_path / "default_team_config.json").write_text("{}")
    monkeypatch.setattr(cc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg_file))
    monkeypatch.setattr(cc, "BLACKLIST_FILE", str(tmp_path / "blacklist.txt"))
    monkeypatch.setattr(cc, "CONTROL_LOG", str(tmp_path / "control_log.json"))
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)
    return cc.app.test_client(), tmp_path


def test_approve_writes_log_and_blacklist(tmp_path, monkeypatch):
    client, path = setup_client(tmp_path, monkeypatch)
    resp = client.post("/approve", data={"cmd": "ls", "summary": "list"})
    assert resp.status_code == 200
    assert resp.data.decode() == "ls"

    blacklist = (path / "blacklist.txt").read_text().strip().splitlines()
    assert blacklist == ["ls"]

    log = (path / "control_log.json").read_text()
    assert "\"received\": \"ls\"" in log
    assert "\"summary\": \"list\"" in log
