import importlib
import json
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


def start_model_server(prompts):
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


def run_agent_for_provider(tmp_path, monkeypatch, provider):
    ctrl_server, ctrl_thread, controller = start_controller(tmp_path, monkeypatch)
    prompts_ollama = []
    prompts_lmstudio = []
    ollama_server, ollama_thread = start_model_server(prompts_ollama)
    lmstudio_server, lmstudio_thread = start_model_server(prompts_lmstudio)

    prompt = "hello model"
    cfg = controller.default_config.copy()
    cfg["prompt"] = prompt
    cfg["provider"] = provider
    (tmp_path / "config.json").write_text(json.dumps(cfg))

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ai_agent = importlib.import_module("ai_agent")
    importlib.reload(ai_agent)

    ctrl_url = f"http://127.0.0.1:{ctrl_server.server_port}"
    ollama_url = f"http://127.0.0.1:{ollama_server.server_port}"
    lmstudio_url = f"http://127.0.0.1:{lmstudio_server.server_port}"

    ai_agent.run_agent(
        controller=ctrl_url,
        ollama=ollama_url,
        lmstudio=lmstudio_url,
        steps=1,
        step_delay=0,
    )

    log_path = tmp_path / "ai_log.json"
    data = json.loads(log_path.read_text())

    ctrl_server.shutdown()
    ollama_server.shutdown()
    lmstudio_server.shutdown()
    ctrl_thread.join()
    ollama_thread.join()
    lmstudio_thread.join()

    return data, prompts_ollama, prompts_lmstudio, prompt


def test_ai_agent_ollama(tmp_path, monkeypatch):
    data, prompts_ollama, prompts_lmstudio, prompt = run_agent_for_provider(
        tmp_path, monkeypatch, "ollama"
    )
    assert data[0]["command"] == "echo hi"
    assert "hi" in data[0]["output"]
    assert prompts_ollama[0] == prompt
    assert prompts_lmstudio == []


def test_ai_agent_lmstudio(tmp_path, monkeypatch):
    data, prompts_ollama, prompts_lmstudio, prompt = run_agent_for_provider(
        tmp_path, monkeypatch, "lmstudio"
    )
    assert data[0]["command"] == "echo hi"
    assert "hi" in data[0]["output"]
    assert prompts_lmstudio[0] == prompt
    assert prompts_ollama == []

