# Prompt + Runbook: Ananta 3er-Lauf (new_software_project über Ollama)

## Zweck
Diese Datei ist der übertragbare Standard-Run für:
- neues Softwareprojekt per LLM-Planung starten (`new_software_project`)
- Task-Materialisierung + Worker-Ausführung via LLM
- 3 frische DB-Läufe automatisiert ausführen
- Ergebnis anhand klarer Kriterien auswerten
- Zielarchitektur prüfen: Goal-scoped Config statt globaler `/config`-Umschaltung

## Rahmenbedingungen
- Repository: `ananta`
- Ziel-Goal (Beispiel): Python-Backend für Fibonacci mit Tests
- LLM-Backend: **Ollama**
- Kein manueller Operatoreingriff während der Bewertungsruns
- Zielmodus: `goal_scoped`
- Legacy/Fallback: `legacy_global_config`

## Wichtige Architekturentscheidung

Der finale Zielmodus ist **goal_scoped**.

Feature-Flags fuer Rollout/Enforcement:
- `goal_scoped_config_enabled` (Default: `true`)
- `goal_scoped_config_enforce_snapshot` (Default: `false`)

Das bedeutet:
- Szenario-Config wird pro Goal über `execution_preferences.config_profile` und optional `execution_preferences.config_overrides` gesetzt.
- Der Runner darf pro Szenario nicht mehr global `/config` patchen.
- Jedes Goal bekommt beim Erzeugen einen immutable effective config snapshot.
- Planner, Propose, Execute und Worker-Routing müssen diesen Snapshot nutzen.
- Der Report muss beweisen, dass `goal_config_source=snapshot` verwendet wurde.

Der alte Modus mit globalem `/config`-Patch bleibt nur als Kompatibilitäts- und Diagnosemodus erhalten.

> Hinweis: Für lokale Dev-Runs das Passwort über `ANANTA_PASSWORD` setzen. Keine Passwörter in diesem Runbook hart codieren.

---

## 1) Vorbereitungs-Checks

```bash
cd /home/krusty/ananta

export ANANTA_BASE_URL=http://localhost:5000
export ANANTA_USER=admin
export ANANTA_PASSWORD='<local-dev-password>'

# 1.1 Containerstatus
docker ps --format 'table {{.Names}}\t{{.Status}}' | rg -n 'ananta-ai-agent-hub|ananta-ai-agent-alpha|ananta-ai-agent-beta|ananta-postgres|ananta-redis' -S

# 1.2 Optional: Hub Health
curl -s "$ANANTA_BASE_URL/health?basic=1"

# 1.3 CLI Runtime auf Ollama setzen, falls notwendig
ananta init --yes --runtime-mode local-dev --llm-backend ollama --model ananta-default

# 1.4 Auth-Check via API
curl -s -X POST "$ANANTA_BASE_URL/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$ANANTA_USER\",\"password\":\"$ANANTA_PASSWORD\"}"
```

---

## 2) Altzustand sauber stoppen

```bash
python scripts/test_env_cleanup.py \
  --hub-base-url "$ANANTA_BASE_URL" \
  --username "$ANANTA_USER" \
  --password "$ANANTA_PASSWORD"

python - <<'PY'
import os, requests, json
b=os.environ.get('ANANTA_BASE_URL','http://localhost:5000')
u=os.environ.get('ANANTA_USER','admin')
p=os.environ['ANANTA_PASSWORD']
s=requests.Session()
t=s.post(b+'/login',json={'username':u,'password':p},timeout=20).json()['data']['access_token']
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

Erwartung:
- `running` darf `false` sein oder auf ein neues Goal springen
- keine alte Goal-ID mit hoher Failure-Schleife

---

## 3) 3er-Abnahmelauf starten

### 3.1 Zielmodus: goal_scoped

Sobald `todo.goal_scoped_config.json` umgesetzt ist, ist dieser Modus der primäre Pfad:

```bash
python scripts/first_goal_acceptance_runner.py \
  --base-url "$ANANTA_BASE_URL" \
  --user "$ANANTA_USER" \
  --password "$ANANTA_PASSWORD" \
  --runs 3 \
  --reset-db \
  --config-mode goal_scoped \
  --goal-text 'Erstelle ein Python-Backend-Projekt zur Berechnung von Fibonacci-Zahlen mit Tests und kurzer Verifikation.' \
  --workspace-root workspace/first-goal-fibonacci \
  --out artifacts/first_goal_acceptance_report_fibonacci.goal_scoped.json
```

Erwartung:
- kein per-scenario `POST /config`
- Goal-Payload enthält `execution_preferences.config_profile`
- optional `execution_preferences.config_overrides`
- Report enthält Config-Evidence pro Run

### 3.2 Legacy/Fallback: globale Config

Nur verwenden, wenn das Deployment goal-scoped Config noch nicht unterstützt:

```bash
python scripts/first_goal_acceptance_runner.py \
  --base-url "$ANANTA_BASE_URL" \
  --user "$ANANTA_USER" \
  --password "$ANANTA_PASSWORD" \
  --runs 3 \
  --reset-db \
  --config-mode legacy_global_config \
  --goal-text 'Erstelle ein Python-Backend-Projekt zur Berechnung von Fibonacci-Zahlen mit Tests und kurzer Verifikation.' \
  --workspace-root workspace/first-goal-fibonacci \
  --out artifacts/first_goal_acceptance_report_fibonacci.legacy_global_config.json
```

Hinweis:
- Dieser Modus darf globale `/config` pro Szenario patchen.
- Dieser Modus ist nicht parallel-sicher.
- Dieser Modus ist kein Beweis für finale Config-Isolation.

### 3.3 Alter Runner ohne Config-Mode

Falls der Runner die neuen Flags noch nicht kennt, entspricht der alte Befehl faktisch dem Legacy-Modus:

```bash
python scripts/first_goal_acceptance_runner.py \
  --base-url "$ANANTA_BASE_URL" \
  --user "$ANANTA_USER" \
  --password "$ANANTA_PASSWORD" \
  --runs 3 \
  --reset-db \
  --goal-text 'Erstelle ein Python-Backend-Projekt zur Berechnung von Fibonacci-Zahlen mit Tests und kurzer Verifikation.' \
  --workspace-root workspace/first-goal-fibonacci \
  --out artifacts/first_goal_acceptance_report_fibonacci.json
```

---

## 4) Erwartete Config-Evidence im Report

Im Zielmodus `goal_scoped` muss jeder Run mindestens diese Felder enthalten:

```json
{
  "run_index": 1,
  "scenario_id": "opencode_ollama_local",
  "config_mode": "goal_scoped",
  "config_profile": "opencode_ollama_local",
  "config_checksum": "<stable checksum>",
  "goal_config_source": "snapshot",
  "effective_config_endpoint_status": "ok",
  "selected_backend": "opencode",
  "selected_provider": "ollama",
  "selected_model": "ananta-default:latest"
}
```

Fehlt `goal_config_source=snapshot`, ist der Lauf für die Zielarchitektur ungültig.

Zusätzlicher API-Check pro Goal:

```bash
python - <<'PY'
import os, requests, json
b=os.environ.get('ANANTA_BASE_URL','http://localhost:5000')
u=os.environ.get('ANANTA_USER','admin')
p=os.environ['ANANTA_PASSWORD']
goal_id='<GOAL_ID>'
s=requests.Session()
t=s.post(b+'/login',json={'username':u,'password':p},timeout=20).json()['data']['access_token']
h={'Authorization':f'Bearer {t}'}
r=s.get(f'{b}/goals/{goal_id}/effective-config',headers=h,timeout=20)
print(r.status_code)
print(json.dumps(r.json().get('data',{}), ensure_ascii=False, indent=2)[:5000])
PY
```

Erwartung:
- Snapshot vorhanden
- Profile/Provenance sichtbar
- Checksum vorhanden
- Secrets redacted/omitted

---

## 5) Live-Monitoring während des Laufs

### 5.1 Autopilot-Status + Circuit/Forward-Fehler

```bash
python - <<'PY'
import os, requests, json, time
b=os.environ.get('ANANTA_BASE_URL','http://localhost:5000')
u=os.environ.get('ANANTA_USER','admin')
p=os.environ['ANANTA_PASSWORD']
s=requests.Session()
t=s.post(b+'/login',json={'username':u,'password':p},timeout=20).json()['data']['access_token']
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

### 5.2 Provider Live Monitor

```bash
python - <<'PY'
import os, requests, json
b=os.environ.get('ANANTA_BASE_URL','http://localhost:5000')
u=os.environ.get('ANANTA_USER','admin')
p=os.environ['ANANTA_PASSWORD']
s=requests.Session()
t=s.post(b+'/login',json={'username':u,'password':p},timeout=20).json()['data']['access_token']
h={'Authorization':f'Bearer {t}'}
r=s.get(b+'/api/system/monitor/providers/live',headers=h,timeout=30)
print(r.status_code)
print(json.dumps((r.json() or {}).get('data',{}), ensure_ascii=False, indent=2)[:4000])
PY
```

### 5.3 Hub/Worker Logs

```bash
docker logs --since 10m ananta-ai-agent-hub-1 2>&1 | rg -n 'autopilot|worker_circuit_open|forward_failed|goal_terminal_task_sweep|dependency_failed|planned_stall|goal_config_source|config_snapshot|effective-config' -S | tail -n 200

docker logs --since 10m ananta-ai-agent-alpha-1 2>&1 | rg -n 'ValidationError|validation_failed|/step/propose|422|goal_config_source|config_snapshot' -S | tail -n 200

docker logs --since 10m ananta-ai-agent-beta-1 2>&1 | rg -n 'ValidationError|validation_failed|/step/propose|422|goal_config_source|config_snapshot' -S | tail -n 200
```

---

## 6) Ergebnisreport auswerten

```bash
ls -l artifacts/first_goal_acceptance_report_fibonacci*.json
sed -n '1,320p' artifacts/first_goal_acceptance_report_fibonacci.goal_scoped.json
```

Minimaler Erfolg:
- `summary.total_runs == 3`
- `summary.repeatability_pass == true`
- Oder mindestens:
  - `completed_runs >= 2`
  - `write_phase_runs == 3`

Zusätzlich im Zielmodus:
- alle Runs haben `config_mode == "goal_scoped"`
- alle Runs haben `goal_config_source == "snapshot"`
- alle Runs haben `config_checksum`
- kein Run zeigt Config-Drift durch globale `/config`

---

## 7) Latenz-/LLM-Diagnostik je Goal

```bash
python scripts/goal_latency_diagnostics.py \
  --base-url "$ANANTA_BASE_URL" \
  --user "$ANANTA_USER" \
  --password "$ANANTA_PASSWORD" \
  --out artifacts/goal_latency_diagnostics_latest.json

python scripts/goal_latency_diagnostics.py \
  --base-url "$ANANTA_BASE_URL" \
  --user "$ANANTA_USER" \
  --password "$ANANTA_PASSWORD" \
  --goal-id <GOAL_ID> \
  --out artifacts/goal_latency_diagnostics_<GOAL_ID>.json
```

Worauf achten:
- `llm_call_profile.calls_seen_real > 0`
- `calls_seen_synthetic` darf existieren, aber reale Metriken müssen vorhanden sein
- `latency_ms_*_real` und Token-Mittelwerte basieren nur auf `estimated=false`

---

## 8) Gezielte Regressionstests

```bash
pytest -q tests/test_provider_observer_service.py
pytest -q tests/test_propose_policy_service.py tests/test_new_project_llm_required_policy.py
pytest -q tests/test_task_scoped_propose_llm_profile_persist.py tests/test_llm_usage.py
pytest -q tests/test_task_queue_service.py
```

Zusätzlich nach Umsetzung von goal-scoped config:

```bash
pytest -q tests/test_goal_config_resolver_service.py
pytest -q tests/test_goal_scoped_config_api.py
pytest -q tests/test_goal_scoped_config_runtime_integration.py
pytest -q tests/test_first_goal_acceptance_runner_config_modes.py
pytest -q tests/e2e/test_parallel_scenario_goal_scoped_config.py
```

---

## 9) Bekannte Stop-Kriterien

Run als ungültig markieren, wenn eines davon eintritt:
- Postgres/Hub Restart während des aktiven Runs
- Autopilot arbeitet auf alter Goal-ID weiter
- Dauerhafter 422-Sturm auf `/tasks/<id>/step/propose`
- Dauerhaft `planning/planned` ohne Fortschritt trotz LLM-Aktivitätssignalen
- Im Zielmodus fehlt `goal_config_source=snapshot`
- Im Zielmodus fehlt `config_checksum`
- Im Zielmodus wird pro Szenario global `/config` gepatcht
- Im Zielmodus ändert eine globale `/config`-Mutation laufende Goal-Entscheidungen

Dann:
1. `test_env_cleanup.py` erneut ausführen
2. Autopilot-Status verifizieren
3. Ursache prüfen: Legacy-Modus vs. goal-scoped Modus
4. 3er-Lauf frisch neu starten

---

## 10) Optionaler CLI-Goal-Flow

```bash
ananta goal --modes

ananta goal \
  --goal 'Erstelle ein Python-Backend-Projekt zur Berechnung von Fibonacci-Zahlen mit Tests und kurzer Verifikation.' \
  --mode new_software_project

ananta goal --tasks --task-status todo
ananta goal --goal-detail <GOAL_ID>
```

Zukünftiger CLI-Zielzustand:

```bash
ananta goal \
  --goal 'Erstelle ein Python-Backend-Projekt zur Berechnung von Fibonacci-Zahlen mit Tests und kurzer Verifikation.' \
  --mode new_software_project \
  --config-profile opencode_ollama_local
```

---

## 11) Erwartetes Abschlussformat

Nach jedem 3er-Lauf dokumentieren:
1. Exakter Startbefehl
2. Commit/Branch/Containerstand
3. Config-Modus: `goal_scoped` oder `legacy_global_config`
4. `first_goal_acceptance_report_*.json` Summary
5. Pro Run: `scenario_id`, `config_profile`, `config_checksum`, `goal_config_source`
6. Gründe für fehlgeschlagene Kriterien pro Run
7. `goal_latency_diagnostics` Kerndaten
8. Nächster konkreter Fixschritt

---

## 12) Zusammenhang mit TODO

Die technische Umsetzung ist in `todo.goal_scoped_config.json` beschrieben.

Wichtigste Runner-/Runbook-Aufgaben daraus:
- `T-010`: Runner support for goal-scoped scenario config
- `T-011`: Parallel scenario add-on test mode
- `T-018`: Runbook dual-mode documentation
- `T-019`: Runner report validation for scoped config evidence
