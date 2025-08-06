import json
import importlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from werkzeug.serving import make_server


def test_agent_runs_against_controller(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    cc = importlib.import_module("controller.controller")
    ai = importlib.import_module("agent.ai_agent")
    importlib.reload(cc)
    importlib.reload(ai)

    cfg_file = tmp_path / "config.json"
    config = {
        "agents": {"default": {}},
        "active_agent": "default",
        "tasks": [{"task": "do"}],
        "api_endpoints": [{"type": "lmstudio", "url": ""}],
        "prompt_templates": {},
    }
    cfg_file.write_text(json.dumps(config))
    (tmp_path / "default_team_config.json").write_text("{}")

    monkeypatch.setattr(cc, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(cc, "CONFIG_FILE", str(cfg_file))
    cc.config_provider = cc.FileConfig(cc.read_config, cc.write_config)

    class LLMHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'"ok"')

    llm_server = HTTPServer(("localhost", 0), LLMHandler)
    threading.Thread(target=llm_server.serve_forever, daemon=True).start()
    llm_url = f"http://localhost:{llm_server.server_port}"
    config["api_endpoints"][0]["url"] = llm_url
    cfg_file.write_text(json.dumps(config))

    server = make_server("localhost", 0, cc.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    controller_url = f"http://localhost:{server.server_port}"

    ai.run_agent(controller=controller_url, endpoints={"lmstudio": llm_url}, steps=1, step_delay=0)

    summary = (tmp_path / "summary_default.txt").read_text()
    assert "ok" in summary
    saved = json.loads(cfg_file.read_text())
    assert saved["tasks"] == []

    server.shutdown()
    llm_server.shutdown()
