
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
    res = requests.post(f"{BASE_URL}/teams", json={
        "name": team_name,
        "description": "Ein Test Scrum Team mit automatischen Artefakten",
        "type": "Scrum"
    }, headers=headers)
    
    if res.status_code != 201:
        print(f"Fehler beim Erstellen des Teams: {res.text}")
        return
    
    team = res.json()
    print(f"Team erstellt: {team['id']}")
    
    # 2. Prüfen ob Tasks erstellt wurden
    time.sleep(1) # Kurz warten
    res = requests.get(f"{BASE_URL}/tasks", headers=headers)
    tasks = res.json()
    
    scrum_tasks = [t for t in tasks if t['title'].startswith(team_name)]
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
