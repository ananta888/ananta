import importlib
import os
import unittest

from psycopg2.extras import Json

from src.db import get_conn


class AgentEndpointTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault(
            "DATABASE_URL", "postgresql://postgres@localhost:5432/ananta"
        )
        self.ai = importlib.reload(importlib.import_module("agent.ai_agent"))
        self.app = self.ai.create_app("default").test_client()
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("TRUNCATE agent.flags, agent.logs, controller.tasks, controller.config RESTART IDENTITY CASCADE")
        conn.commit()
        cur.close()
        conn.close()

    def test_health_stop_restart(self):
        health = self.app.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()["status"], "ok")

        stop_resp = self.app.post("/stop")
        self.assertEqual(stop_resp.status_code, 200)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT value FROM agent.flags WHERE name='stop'")
        self.assertEqual(cur.fetchone()[0], "1")
        cur.close()
        conn.close()

        restart_resp = self.app.post("/restart")
        self.assertEqual(restart_resp.status_code, 200)
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM agent.flags WHERE name='stop'")
        self.assertIsNone(cur.fetchone())
        cur.close()
        conn.close()

    def test_logs_and_tasks(self):
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO agent.logs (agent, level, message) VALUES ('default','INFO','hi')"
        )
        cur.execute(
            "INSERT INTO controller.tasks (task, agent) VALUES ('t1','default')"
        )
        cur.execute(
            "INSERT INTO controller.config (data) VALUES (%s)",
            (Json({"agents": {"default": {"current_task": "c"}}}),),
        )
        conn.commit()
        cur.close()
        conn.close()

        logs = self.app.get("/logs")
        self.assertEqual(logs.status_code, 200)
        data = logs.get_json()
        self.assertEqual(data["agent"], "default")
        self.assertEqual(len(data["logs"]), 1)

        tasks = self.app.get("/tasks")
        self.assertEqual(tasks.status_code, 200)
        tdata = tasks.get_json()
        self.assertEqual(tdata["current_task"], "c")
        self.assertEqual(tdata["tasks"], [{"task": "t1", "agent": "default", "template": None}])

    def test_no_approve_route(self):
        app = self.ai.create_app().test_client().application
        routes = {rule.rule for rule in app.url_map.iter_rules()}
        self.assertNotIn("/approve", routes)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
