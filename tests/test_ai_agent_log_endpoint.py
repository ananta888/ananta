import threading
import urllib.error
import urllib.request

from werkzeug.serving import make_server


def test_log_endpoint_serves_buffer(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from importlib import reload
    import agent.ai_agent as ai_agent
    reload(ai_agent)
    AGENTS, ControllerAgent, app = ai_agent.AGENTS, ai_agent.ControllerAgent, ai_agent.app

    agent = ControllerAgent("test")
    agent.log_status("hello")
    AGENTS["test"] = agent

    server = make_server("localhost", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://localhost:{server.server_port}/agent/test/log"
    with urllib.request.urlopen(url) as r:
        data = r.read().decode()
    assert "hello" in data
    assert (tmp_path / "ai_log_test.json").read_text().strip().endswith("hello")

    server.shutdown()
    thread.join()
    AGENTS.clear()


def test_log_endpoint_unknown_agent(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from importlib import reload
    import agent.ai_agent as ai_agent
    reload(ai_agent)
    app = ai_agent.app

    server = make_server("localhost", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://localhost:{server.server_port}/agent/unknown/log"
    try:
        urllib.request.urlopen(url)
    except urllib.error.HTTPError as e:
        assert e.code == 404
    else:
        assert False, "Expected HTTPError"

    server.shutdown()
    thread.join()
