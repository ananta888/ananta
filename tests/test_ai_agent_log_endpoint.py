import threading
import urllib.request
from werkzeug.serving import make_server
from importlib import reload
import logging

import agent.ai_agent as ai_agent
from src.db import get_conn


def test_log_endpoint_serves_buffer(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
    reload(ai_agent)
    logging.getLogger().handlers = []
    ai_agent.LogManager.setup("agent")
    agent = ai_agent.ControllerAgent("test")
    agent.log_status("hello")
    ai_agent.AGENTS["test"] = agent
    server = make_server("localhost", 0, ai_agent.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://localhost:{server.server_port}/agent/test/log"
    with urllib.request.urlopen(url) as r:
        data = r.read().decode()
    assert "hello" in data
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT message FROM agent.logs WHERE agent='test'")
    msgs = [m[0] for m in cur.fetchall()]
    cur.close()
    conn.close()
    assert any("hello" in m for m in msgs)
    server.shutdown()
    thread.join()
    ai_agent.AGENTS.clear()


def test_log_endpoint_unknown_agent(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
    reload(ai_agent)
    logging.getLogger().handlers = []
    ai_agent.LogManager.setup("agent")
    server = make_server("localhost", 0, ai_agent.app)
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


def test_delete_log_endpoint(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost:5432/ananta")
    reload(ai_agent)
    logging.getLogger().handlers = []
    ai_agent.LogManager.setup("agent")
    agent = ai_agent.ControllerAgent("test")
    agent.log_status("remove")
    ai_agent.AGENTS["test"] = agent
    server = make_server("localhost", 0, ai_agent.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://localhost:{server.server_port}/agent/test/log"
    req = urllib.request.Request(url, method="DELETE")
    with urllib.request.urlopen(req) as r:
        assert r.status == 204
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT message FROM agent.logs WHERE agent='test'")
    assert cur.fetchall() == []
    cur.close()
    conn.close()
    server.shutdown()
    thread.join()
    ai_agent.AGENTS.clear()
