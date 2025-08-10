import importlib
import os
import unittest

try:
    from psycopg2.extras import Json  # type: ignore
    from src.db import get_conn, init_db  # type: ignore
    HAVE_PG = True
except Exception:  # pragma: no cover
    HAVE_PG = False


@unittest.skipUnless(HAVE_PG, "psycopg2 not installed; skipping controller endpoint tests")
class ControllerEndpointTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault(
            "DATABASE_URL", "postgresql://postgres@localhost:5432/ananta"
        )
        # Import after setting env
        self.ctrl = importlib.reload(importlib.import_module("controller.controller"))
        self.app = self.ctrl.app.test_client()
        # Clean tables
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "TRUNCATE agent.flags, agent.logs, controller.blacklist, controller.tasks, controller.control_log, controller.config RESTART IDENTITY CASCADE"
        )
        conn.commit()
        cur.close()
        conn.close()

    def test_next_config_and_add_task(self):
        # initially no task
        resp = self.app.get("/next-config")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["tasks"], [])
        self.assertEqual(resp.get_json()["templates"], {})

        # add task and then fetch
        queued = self.app.post("/agent/add_task", json={"task": "hello"})
        self.assertEqual(queued.status_code, 200)
        data = queued.get_json()
        self.assertEqual(data["status"], "queued")

        nxt = self.app.get("/next-config")
        self.assertEqual(nxt.status_code, 200)
        self.assertEqual(nxt.get_json()["tasks"], ["hello"])  # consumed

        # No more tasks
        nxt2 = self.app.get("/next-config")
        self.assertEqual(nxt2.get_json()["tasks"], [])

    def test_blacklist_and_next_task_skips(self):
        # add tasks
        self.app.post("/agent/add_task", json={"task": "rm -rf /"})
        self.app.post("/agent/add_task", json={"task": "safe"})
        # blacklist dangerous
        bl = self.app.post("/controller/blacklist", json={"task": "rm -rf /"})
        self.assertEqual(bl.status_code, 200)
        # next task should skip blacklisted and return safe
        nxt = self.app.get("/controller/next-task")
        self.assertEqual(nxt.status_code, 200)
        self.assertEqual(nxt.get_json()["task"], "safe")

    def test_config_update_and_export_and_approve(self):
        # default config
        cfg = self.app.get("/config")
        self.assertEqual(cfg.status_code, 200)
        self.assertIn("api_endpoints", cfg.get_json())
        # update
        up = self.app.post("/config/api_endpoints", json={"api_endpoints": ["/a", "/b"]})
        self.assertEqual(up.status_code, 200)
        # read back
        cfg2 = self.app.get("/config")
        self.assertEqual(cfg2.get_json()["api_endpoints"], ["/a", "/b"])
        # approve writes control_log
        ap = self.app.post("/approve", json={"ok": True})
        self.assertEqual(ap.status_code, 200)
        # export returns logs and config
        ex = self.app.get("/export")
        self.assertEqual(ex.status_code, 200)
        exj = ex.get_json()
        self.assertIn("config", exj)
        self.assertIn("logs", exj)
        self.assertGreaterEqual(len(exj["logs"]), 1)

    def test_agent_logs_and_tasks_listing(self):
        # Insert logs and tasks directly
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO agent.logs (agent, level, message) VALUES ('alice', 20, 'hi')")
        cur.execute("INSERT INTO controller.tasks (task, agent) VALUES ('t1','alice')")
        cur.execute("INSERT INTO controller.tasks (task, agent) VALUES ('t2', NULL)")
        conn.commit()
        cur.close()
        conn.close()

        # logs GET and DELETE
        logs = self.app.get("/agent/alice/log?limit=10")
        self.assertEqual(logs.status_code, 200)
        self.assertEqual(len(logs.get_json()), 1)
        self.assertEqual(logs.get_json()[0]["message"], "hi")

        d = self.app.delete("/agent/alice/log")
        self.assertEqual(d.status_code, 200)
        # After delete, no logs
        logs2 = self.app.get("/agent/alice/log")
        self.assertEqual(logs2.get_json(), [])

        # tasks list for agent includes specific and null-agent tasks
        t = self.app.get("/agent/alice/tasks")
        self.assertEqual(t.status_code, 200)
        self.assertEqual(t.get_json()["tasks"], ["t1", "t2"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
