# Prompt + Runbook: Ananta 3er-Lauf (new_software_project über Ollama)

## Zweck
Diese Datei ist der übertragbare Standard-Run für:
- neues Softwareprojekt per LLM-Planung starten (`new_software_project`)
- Task-Materialisierung + Worker-Ausführung via LLM
- 3 frische DB-Läufe automatisiert ausführen
- Ergebnis anhand klarer Kriterien auswerten

## Rahmenbedingungen
- Repository: `ananta`
- Ziel-Goal (Beispiel): Python-Backend für Fibonacci mit Tests
- LLM-Backend: **Ollama**
- Kein manueller Operatoreingriff während der Bewertungsruns

---

## 1) Vorbereitungs-Checks (exakt)

```bash
cd /home/krusty/ananta

# 1.1 Containerstatus

docker ps --format 'table {{.Names}}\t{{.Status}}' | rg -n 'ananta-ai-agent-hub|ananta-ai-agent-alpha|ananta-ai-agent-beta|ananta-postgres|ananta-redis' -S

# 1.2 Optional: Hub Health
curl -s http://localhost:5000/health?basic=1

# 1.3 CLI Runtime auf Ollama setzen (falls notwendig)
ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default

# 1.4 Auth-Check via API (liefert Bearer Token)
curl -s -X POST http://localhost:5000/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"AnantaLocalDevAdmin123!"}'
```

---

## 2) Altzustand sauber stoppen (wichtig vor jedem 3er-Lauf)

```bash
# 2.1 Test-Umgebung bereinigen (inkl. Autopilot stop/reset)
python scripts/test_env_cleanup.py \
  --hub-base-url http://localhost:5000 \
  --username admin \
  --password 'AnantaLocalDevAdmin123!'

# 2.2 Verifizieren: kein alter Autopilot hängt
python - <<'PY'
import requests, json
b='http://localhost:5000'
s=requests.Session()
t=s.post(b+'/login',json={'username':'admin','password':'AnantaLocalDevAdmin123!'},timeout=20).json()['data']['access_token']
h={'Authorization':f'Bearer {t}'}
status=s.get(b+'/tasks/autopilot/status',headers=h,timeout=20).json().get('data',{})
print(json.dumps({
  'running': status.get('running'),
  'goal': status.get('goal'),
  'dispatched_count': status.get('dispatched_count'),
  'failed_count': status.get('failed_count'),
  'last_error': status.get('last_error')
}, indent=2))
PY
```

Erwartung vor neuem Lauf:
- `running` darf `false` sein oder auf **neues** Goal springen
- keine alte Goal-ID mit hoher Failure-Schleife

---

## 3) 3er-Abnahmelauf starten (frische DB je Run)

```bash
python scripts/first_goal_acceptance_runner.py \
  --base-url http://localhost:5000 \
  --user admin \
  --password 'AnantaLocalDevAdmin123!' \
  --runs 3 \
  --reset-db \
  --goal-text 'Erstelle ein Python-Backend-Projekt zur Berechnung von Fibonacci-Zahlen mit Tests und kurzer Verifikation.' \
  --workspace-root workspace/first-goal-fibonacci \
  --out artifacts/first_goal_acceptance_report_fibonacci.json
```

---

## 4) Live-Monitoring während des Laufs (parallel)

### 4.1 Autopilot-Status + Circuit/Forward-Fehler
```bash
python - <<'PY'
import requests, json, time
b='http://localhost:5000'
s=requests.Session()
t=s.post(b+'/login',json={'username':'admin','password':'AnantaLocalDevAdmin123!'},timeout=20).json()['data']['access_token']
h={'Authorization':f'Bearer {t}'}
for _ in range(20):
    d=s.get(b+'/tasks/autopilot/status',headers=h,timeout=20).json().get('data',{})
    out={
      'running': d.get('running'),
      'goal': d.get('goal'),
      'dispatched_count': d.get('dispatched_count'),
      'completed_count': d.get('completed_count'),
      'failed_count': d.get('failed_count'),
      'last_error': d.get('last_error'),
      'open_workers': (d.get('circuit_breakers') or {}).get('open_workers'),
      'forward_http_errors': (d.get('circuit_breakers') or {}).get('forward_http_errors',{}).get('counts',{})
    }
    print(json.dumps(out, ensure_ascii=False))
    time.sleep(15)
PY
```

### 4.2 Provider Live Monitor (Hub-Sicht)
```bash
# Achtung: Endpoint braucht Auth. Ohne Token kommt unauthorized.
python - <<'PY'
import requests, json
b='http://localhost:5000'
s=requests.Session()
t=s.post(b+'/login',json={'username':'admin','password':'AnantaLocalDevAdmin123!'},timeout=20).json()['data']['access_token']
h={'Authorization':f'Bearer {t}'}
r=s.get(b+'/api/system/monitor/providers/live',headers=h,timeout=30)
print(r.status_code)
print(json.dumps((r.json() or {}).get('data',{}), ensure_ascii=False, indent=2)[:4000])
PY
```

### 4.3 Hub/Worker Logs auf Validierungs- und Transportfehler
```bash
# Hub
docker logs --since 10m ananta-ai-agent-hub-1 2>&1 | rg -n 'autopilot|worker_circuit_open|forward_failed|goal_terminal_task_sweep|dependency_failed|planned_stall' -S | tail -n 200

# Worker alpha
docker logs --since 10m ananta-ai-agent-alpha-1 2>&1 | rg -n 'ValidationError|validation_failed|/step/propose|422' -S | tail -n 200

# Worker beta
docker logs --since 10m ananta-ai-agent-beta-1 2>&1 | rg -n 'ValidationError|validation_failed|/step/propose|422' -S | tail -n 200
```

---

## 5) Ergebnisreport auswerten

```bash
ls -l artifacts/first_goal_acceptance_report_fibonacci.json
sed -n '1,260p' artifacts/first_goal_acceptance_report_fibonacci.json
```

Minimaler Erfolg:
- `summary.total_runs == 3`
- `summary.repeatability_pass == true` (Zielzustand)
- Oder mindestens:
  - `completed_runs >= 2`
  - `write_phase_runs == 3` (Progress bis Schreibphase in allen 3 Läufen)

---

## 6) Latenz-/LLM-Diagnostik je Goal

```bash
# Letztes Goal automatisch
python scripts/goal_latency_diagnostics.py \
  --base-url http://localhost:5000 \
  --user admin \
  --password 'AnantaLocalDevAdmin123!' \
  --out artifacts/goal_latency_diagnostics_latest.json

# Mit fixer Goal-ID
python scripts/goal_latency_diagnostics.py \
  --base-url http://localhost:5000 \
  --user admin \
  --password 'AnantaLocalDevAdmin123!' \
  --goal-id <GOAL_ID> \
  --out artifacts/goal_latency_diagnostics_<GOAL_ID>.json
```

Worauf achten:
- `llm_call_profile.calls_seen_real > 0`
- `calls_seen_synthetic` darf existieren, aber reale Metriken müssen vorhanden sein
- `latency_ms_*_real` und Token-Mittelwerte basieren nur auf `estimated=false`

---

## 7) Gezielte Regressionstests (exakt)

```bash
pytest -q tests/test_provider_observer_service.py
pytest -q tests/test_propose_policy_service.py tests/test_new_project_llm_required_policy.py
pytest -q tests/test_task_scoped_propose_llm_profile_persist.py tests/test_llm_usage.py
pytest -q tests/test_task_queue_service.py
```

Optional vollständiger Block:
```bash
pytest -q \
  tests/test_provider_observer_service.py \
  tests/test_propose_policy_service.py \
  tests/test_new_project_llm_required_policy.py \
  tests/test_task_scoped_propose_llm_profile_persist.py \
  tests/test_llm_usage.py \
  tests/test_task_queue_service.py
```

---

## 8) Bekannte Stop-Kriterien (Run ungültig / neu starten)

Run als ungültig markieren, wenn eines davon eintritt:
- Postgres/Hub Restart während des aktiven Runs (`database system is shutting down` o.ä.)
- Autopilot arbeitet auf alter Goal-ID weiter (stale session)
- Dauerhafter 422-Sturm auf `/tasks/<id>/step/propose`
- Dauerhaft `planning/planned` ohne Fortschritt trotz LLM-Aktivitätssignalen

Dann:
1. `test_env_cleanup.py` erneut ausführen
2. Autopilot-Status verifizieren
3. 3er-Lauf frisch neu starten

---

## 9) Optionaler CLI-Goal-Flow (ananta-Kommandos)

Wenn du statt Runner manuell ein Goal erzeugen willst:

```bash
# Verfügbare Modi prüfen
ananta goal --modes

# Goal mit Mode new_software_project erstellen (wenn CLI-Flags in deiner Version verfügbar)
ananta goal --goal 'Erstelle ein Python-Backend-Projekt zur Berechnung von Fibonacci-Zahlen mit Tests und kurzer Verifikation.' --mode new_software_project

# Goal/Tasks verfolgen
ananta goal --tasks --task-status todo
ananta goal --goal-detail <GOAL_ID>
```

Hinweis: Für reproduzierbare Abnahme ist der Runner aus Abschnitt 3 der primäre Pfad.

---

## 10) Erwartetes Abschlussformat (für Berichte)

Nach jedem 3er-Lauf dokumentieren:
1. Exakter Startbefehl
2. Commit/Branch/Containerstand
3. `first_goal_acceptance_report_*.json` Summary
4. Gründe für fehlgeschlagene Kriterien (pro Run)
5. `goal_latency_diagnostics` Kerndaten (real vs synthetic)
6. Nächster konkreter Fixschritt
