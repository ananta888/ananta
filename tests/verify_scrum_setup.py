
import requests
import uuid
import time

BASE_URL = "http://localhost:5000" # Angenommen der Hub läuft hier
AUTH_TOKEN = "hubsecret" # Aus docker-compose.yml

def test_create_scrum_team():
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}
    team_name = f"Test Scrum Team {uuid.uuid4().hex[:6]}"
    
    # 1. Team erstellen
    print(f"Erstelle Team: {team_name}")
    res = requests.post(f"{BASE_URL}/teams/setup-scrum", json={
        "name": team_name
    }, headers=headers)
    
    if res.status_code != 201:
        print(f"Fehler beim Erstellen des Teams: {res.status_code}")
        print(f"Antwort: {res.text}")
        return
    
    try:
        team_data = res.json()
        print(f"Antwort-JSON: {team_data}")
        # Das Backend gibt oft {"status": "success", "team": {...}} zurück
        team = team_data.get("team") or team_data.get("data", {}).get("team") or team_data
        print(f"Team Objekt: {team}")
        team_id = team.get("id")
        print(f"Team erstellt: {team_id}")
    except Exception as e:
        print(f"Fehler beim Parsen der Antwort: {e}")
        print(f"Rohe Antwort: {res.text}")
        return
    
    # 2. Prüfen ob Tasks erstellt wurden
    time.sleep(1) # Kurz warten
    res = requests.get(f"{BASE_URL}/tasks", headers=headers)
    tasks_data = res.json()
    print(f"Tasks Antwort-JSON: {tasks_data}")
    
    tasks = tasks_data if isinstance(tasks_data, list) else tasks_data.get("data", [])
    if not isinstance(tasks, list):
         # Falls es Paging ist
         tasks = tasks.get("items", []) if isinstance(tasks, dict) else []

    scrum_tasks = [t for t in tasks if isinstance(t, dict) and t.get('title', '').startswith(team_name)]
    print(f"Gefundene Tasks für das Team: {len(scrum_tasks)}")
    
    expected_titles = [
        "Scrum Backlog",
        "Sprint Board Setup",
        "Burndown Chart",
        "Roadmap",
        "Setup & Usage Instructions"
    ]
    
    for title in expected_titles:
        found = any(title in t['title'] for t in scrum_tasks)
        print(f"Task '{title}' vorhanden: {found}")
        if not found:
            print(f"WARNUNG: Task '{title}' wurde nicht gefunden!")

if __name__ == "__main__":
    try:
        test_create_scrum_team()
    except Exception as e:
        print(f"Test fehlgeschlagen: {e}")
