import requests
import time
import sys

BASE_URL = "http://localhost:5000"
MAX_RETRIES = 10
DELAY = 5

def wait_for_ready():
    for i in range(MAX_RETRIES):
        try:
            print(f"Versuch {i+1}: Prüfe /ready...")
            resp = requests.get(f"{BASE_URL}/ready", timeout=5)
            if resp.status_code == 200:
                print("Agent ist bereit!")
                return True
        except Exception as e:
            print(f"Fehler: {e}")
        time.sleep(DELAY)
    return False

def trigger_dummy_task():
    print("Triggere Dummy-Task...")
    data = {
        "prompt": "echo 'Smoke Test OK'",
        "task_id": "smoke-test"
    }
    # Propose
    resp = requests.post(f"{BASE_URL}/step/propose", json=data)
    if resp.status_code != 200:
        print(f"Propose fehlgeschlagen: {resp.text}")
        return False
    
    cmd = resp.json().get("command")
    print(f"Vorgeschlagener Befehl: {cmd}")
    
    # Execute
    exec_data = {
        "command": cmd,
        "task_id": "smoke-test"
    }
    resp = requests.post(f"{BASE_URL}/step/execute", json=exec_data)
    if resp.status_code == 200:
        print("Task erfolgreich ausgeführt!")
        print(f"Output: {resp.json().get('stdout')}")
        return True
    else:
        print(f"Execute fehlgeschlagen: {resp.text}")
        return False

if __name__ == "__main__":
    if wait_for_ready():
        if trigger_dummy_task():
            print("E2E Smoke Test ERFOLGREICH")
            sys.exit(0)
    
    print("E2E Smoke Test FEHLGESCHLAGEN")
    sys.exit(1)
