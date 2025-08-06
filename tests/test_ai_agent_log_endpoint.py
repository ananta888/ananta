import threading
import urllib.error
import urllib.request

from werkzeug.serving import make_server


def test_log_endpoint_serves_buffer():
    from agent.ai_agent import AGENTS, ControllerAgent, app

    agent = ControllerAgent("test")
    agent.log_status("hello")
    AGENTS["test"] = agent

    server = make_server("localhost", 0, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = f"http://localhost:{server.server_port}/agent/test/log"
    with urllib.request.urlopen(url) as r:
        data = r.read().decode()
    assert data == "hello"

    server.shutdown()
    thread.join()
    AGENTS.clear()


def test_log_endpoint_unknown_agent():
    from agent.ai_agent import app

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
