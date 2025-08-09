import time
import requests


def main(controller_url: str = "http://localhost:5000") -> None:
    """Continuously poll the controller for tasks and approve them."""
    while True:
        resp = requests.get(f"{controller_url}/next-config")
        data = resp.json()
        for task in data.get("tasks", []):
            result = {
                "task": task,
                "result": f"Executed {task}",
            }
            requests.post(f"{controller_url}/approve", json=result)
        time.sleep(1)


if __name__ == "__main__":
    main()
