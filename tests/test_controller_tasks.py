import importlib
import unittest


class ControllerTasksTests(unittest.TestCase):
    def setUp(self):
        self.controller = importlib.reload(importlib.import_module("controller.controller"))
        self.app = self.controller.app.test_client()

    def test_add_and_list_tasks(self):
        # Initially empty
        res0 = self.app.get("/agent/default/tasks")
        self.assertEqual(res0.status_code, 200)
        self.assertEqual(res0.get_json()["tasks"], [])

        # Add a task
        add = self.app.post("/agent/add_task", json={"task": "t1"})
        self.assertEqual(add.status_code, 200)
        self.assertEqual(add.get_json()["status"], "queued")

        # List tasks
        res = self.app.get("/agent/default/tasks")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.get_json()["tasks"], ["t1"])  # controller stores plain strings

        # next-config should pop the task
        nc = self.app.get("/next-config")
        self.assertEqual(nc.status_code, 200)
        data = nc.get_json()
        self.assertIn("tasks", data)
        self.assertEqual(data["tasks"], ["t1"])  # returned then removed

        # After pop, list should be empty again
        res2 = self.app.get("/agent/default/tasks")
        self.assertEqual(res2.get_json()["tasks"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
