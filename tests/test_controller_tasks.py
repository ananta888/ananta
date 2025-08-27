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
        tasks = res.get_json()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["task"], "t1")

        # Pop the task via /tasks/next
        nxt = self.app.get("/tasks/next?agent=default")
        self.assertEqual(nxt.status_code, 200)
        self.assertEqual(nxt.get_json()["task"], "t1")

        # After pop, list should be empty again
        res2 = self.app.get("/agent/default/tasks")
        self.assertEqual(res2.get_json()["tasks"], [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
