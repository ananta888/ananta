import importlib
import os
from pathlib import Path
import controller


ROOT = Path(__file__).resolve().parent.parent


def test_load_team_config():
    path = ROOT / "default_team_config.json"
    cfg = controller.load_team_config(str(path))
    assert cfg.get("pipeline_order"), "pipeline_order should not be empty"
    assert "Architect" in cfg.get("agents", {})
    assert cfg.get("prompt_templates", {}).get("Architect", "").startswith("Du bist der Software-Architekt")


def test_read_config_initialises_from_team_config(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ctrl = importlib.reload(controller)
    cfg = ctrl.read_config()
    assert "Architect" in cfg["agents"]
    assert cfg["pipeline_order"][0] == "Architect"
    assert (tmp_path / "config.json").exists()

