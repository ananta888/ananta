# Threading E2E Test – thr-014 & thr-015

Manuelle Integrationstests für die parallele Task-Verarbeitung im Autopilot-Tick-Engine.
Voraussetzung: Hub läuft, mindestens 2 Worker (`alpha`, `beta`) sind online, Ollama verfügbar.

---

## thr-014: 2 Tasks parallel gegen echte Worker

### Setup

```bash
# 1. Zwei unabhängige Tasks in einem Goal anlegen
GOAL_ID=$(ananta goals create "Threading E2E Test" | jq -r '.id')

ananta tasks create \
  --goal "$GOAL_ID" \
  --title "Task A – parallel test" \
  --description "echo 'Task A done'" \
  --team-id "$(ananta teams list | jq -r '.[0].id')"

ananta tasks create \
  --goal "$GOAL_ID" \
  --title "Task B – parallel test" \
  --description "echo 'Task B done'" \
  --team-id "$(ananta teams list | jq -r '.[0].id')"

# 2. Autopilot mit max_concurrency=2 und balanced starten
curl -s -X POST http://localhost:8000/tasks/autopilot/start \
  -H "Content-Type: application/json" \
  -d '{"max_concurrency": 2, "security_level": "balanced", "goal": "'"$GOAL_ID"'"}'
```

### Monitoring während des Tests

```bash
# GPU-Auslastung live beobachten (parallele Peaks = Erfolg)
nvidia-smi dmon -s u -d 1

# Hub-Logs filtern (task_id-Präfix zeigt parallele Ausführung)
journalctl -u ananta-hub -f | grep '\[tick\]\[task_id='

# Autopilot-Status abfragen
watch -n 2 'curl -s http://localhost:8000/tasks/autopilot/status | jq "{dispatched_count, completed_count, failed_count, effective_max_concurrency}"'
```

### Akzeptanzkriterien

| Kriterium | Prüfmethode | Erwartetes Ergebnis |
|-----------|-------------|---------------------|
| GPU-Parallelität | nvidia-smi dmon | Beide Tasks zeigen überlappende GPU-Last, kein sequenzieller Wechsel |
| Log-Überlappung | Hub-Logs | Log-Zeilen von Task A und Task B haben überlappende Timestamps für `propose`-Start |
| Task-Status korrekt | `ananta tasks list --goal $GOAL_ID` | Beide Tasks `completed` oder `failed`, kein Status-Durcheinander |
| Zähler korrekt | `GET /tasks/autopilot/status` | `dispatched_count=2`, `completed_count` ≥ 0, `failed_count` ≥ 0, Summe = 2 |
| Keine SQLAlchemy-Exceptions | Hub-Logs | Kein `DetachedInstanceError`, kein `sqlalchemy.exc.*` |
| effective_max_concurrency | `GET /tasks/autopilot/status` | Wert `2` (bei `security_level=balanced`) |

### Cleanup

```bash
curl -s -X POST http://localhost:8000/tasks/autopilot/stop
```

---

## thr-015: Graceful Stop während paralleler Ausführung

### Setup

```bash
# Autopilot mit 2 langen Tasks starten (sleep simuliert laufenden Task)
GOAL_ID=$(ananta goals create "Graceful Stop Test" | jq -r '.id')

for i in 1 2; do
  ananta tasks create \
    --goal "$GOAL_ID" \
    --title "Long Task $i" \
    --description "sleep 30 && echo done" \
    --team-id "$(ananta teams list | jq -r '.[0].id')"
done

curl -s -X POST http://localhost:8000/tasks/autopilot/start \
  -H "Content-Type: application/json" \
  -d '{"max_concurrency": 2, "security_level": "balanced", "goal": "'"$GOAL_ID"'"}'
```

### Test-Ablauf

```bash
# 1. Warten bis beide Tasks in proposing/in_progress sind
watch -n 1 'ananta tasks list --goal $GOAL_ID | jq ".[].status"'

# 2. Stop absetzen während Tasks laufen
time curl -s -X POST http://localhost:8000/tasks/autopilot/stop | jq

# 3. Sofort Task-Status prüfen
ananta tasks list --goal $GOAL_ID | jq ".[].status"

# 4. Autopilot neu starten und prüfen ob sauber weiterläuft
curl -s -X POST http://localhost:8000/tasks/autopilot/start \
  -H "Content-Type: application/json" \
  -d '{"max_concurrency": 2, "security_level": "balanced"}'

sleep 10
curl -s http://localhost:8000/tasks/autopilot/status | jq '{running, tick_count, dispatched_count}'
```

### Akzeptanzkriterien

| Kriterium | Prüfmethode | Erwartetes Ergebnis |
|-----------|-------------|---------------------|
| Stop kehrt schnell zurück | `time curl .../stop` | Antwort innerhalb 10s |
| Kein Zombie-Status | `ananta tasks list` | Alle Tasks: `completed`, `failed` oder `todo` – niemals `proposing`/`in_progress` nach Stop |
| Sauberer Neustart | `GET /tasks/autopilot/status` nach re-start | `running=true`, `tick_count` steigt, kein Fehler |
| Keine offenen DB-Connections | PostgreSQL | `SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE '%ananta%' AND state = 'idle in transaction'` → 0 |
| Kein Thread-Leak | Hub-Prozess | `ps -T -p $(pgrep -f ananta-hub)` zeigt nach Stop keine übriggebliebenen `autonomous-scrum-loop`-Threads |

---

## Schnell-Checkliste für beide Tests

```
[ ] nvidia-smi zeigt überlappende GPU-Peaks bei thr-014
[ ] Log-Timestamps überlappen für parallele Tasks
[ ] Alle Task-Status korrekt nach Completion
[ ] effective_max_concurrency korrekt im Status
[ ] Stop < 10s bei thr-015
[ ] Kein Zombie-Status nach Stop
[ ] Sauberer Neustart nach Stop
[ ] Keine SQLAlchemy-Exceptions in Logs
```
