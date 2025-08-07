import json
import logging
import pytest
import controller.controller as cc


@pytest.fixture(autouse=True)
def controller_config(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({}))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg))
    log_file = tmp_path / "controller.log"
    monkeypatch.setattr(cc, "LOG_FILE", str(log_file))
    for h in list(logging.getLogger().handlers):
        if isinstance(h, logging.FileHandler):
            logging.getLogger().removeHandler(h)
    fh = logging.FileHandler(log_file)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().addHandler(fh)
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)
    yield

