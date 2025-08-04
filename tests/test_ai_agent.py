import importlib
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from werkzeug.serving import make_server


def start_controller(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    controller = importlib.import_module("controller")
    importlib.reload(controller)
    server = make_server("127.0.0.1", 0, controller.app)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread, controller


def start_ollama_server(prompts):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body.decode())
            except Exception:
                data = {}
            prompts.append(data.get("prompt"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"response": "echo hi"}')

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    return server, thread


def test_ai_agent_simulation(tmp_path, monkeypatch):
    ctrl_server, ctrl_thread, controller = start_controller(tmp_path, monkeypatch)
    prompts = []
    ollama_server, ollama_thread = start_ollama_server(prompts)

    # Create a config containing a custom prompt
    prompt = "hello model"
    cfg = controller.default_config.copy()
    cfg["prompt"] = prompt
    (tmp_path / "config.json").write_text(json.dumps(cfg))

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ai_agent = importlib.import_module("ai_agent")
    importlib.reload(ai_agent)

    ctrl_url = f"http://127.0.0.1:{ctrl_server.server_port}"
    ollama_url = f"http://127.0.0.1:{ollama_server.server_port}"

    ai_agent.run_agent(controller=ctrl_url, ollama=ollama_url, steps=1, step_delay=0)

    log_path = tmp_path / "ai_log.json"
    data = json.loads(log_path.read_text())
    assert data[0]["command"] == "echo hi"
    assert "hi" in data[0]["output"]
    assert prompts[0] == prompt

    ctrl_server.shutdown()
    ollama_server.shutdown()
    ctrl_thread.join()
    ollama_thread.join()
