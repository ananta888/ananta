import importlib
import time
import json
import os
import sys

# Ensure project root is on sys.path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Import the controller app
ctrl = importlib.reload(importlib.import_module("controller.controller"))
app = ctrl.app.test_client()


def main():
    task = f"e2e-repro-{int(time.time())}"
    agent = "Architect"

    # Add task
    add = app.post("/agent/add_task", json={"task": task, "agent": agent})
    print("add_status:", add.status_code, add.get_json())

    # Immediately list tasks for the agent
    lst = app.get(f"/agent/{agent}/tasks")
    print("list_status:", lst.status_code)
    data = lst.get_json()
    print("list_payload:", json.dumps(data, ensure_ascii=False))

    # Check whether the task is present
    tasks = (data or {}).get("tasks", [])
    present = any((isinstance(t, dict) and t.get("task") == task) or (t == task) for t in tasks)
    print("task_present:", present)


if __name__ == "__main__":
    main()
