from pathlib import Path
from src.config import ConfigManager, ConfigSchema


def test_config_manager_load_save(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    mgr = ConfigManager(cfg_path)
    cfg = ConfigSchema(active_agent="a", controller_url="u")
    mgr.save(cfg)
    loaded = mgr.load()
    assert loaded.active_agent == "a"
    assert loaded.controller_url == "u"
