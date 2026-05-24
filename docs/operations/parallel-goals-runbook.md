# Parallel Goals — Betriebshandbuch

Dieses Runbook beschreibt Betrieb, Diagnose und Recovery fuer parallele Goal-Planning-Runs mit LMStudio oder Ollama.

---

## Empfohlene Profilwerte

| Runtime-Profil | `parallel_goal_planning_max_concurrency` | `queue_wait_timeout_seconds` | `planning_timeout_s` | `circuit_breaker_threshold` | `rate_limit_rpm` |
|----------------|------------------------------------------|------------------------------|----------------------|-----------------------------|-----------------|
| `lmstudio_laptop` | 1 | 120 | 600 | 3 | 10 |
| `lmstudio_desktop` | 2 | 180 | 600 | 5 | 20 |
| `ollama_rtx3080` | 3 | 120 | 300 | 5 | 0 (kein Limit) |
| `local-dev` | 1 | 60 | 300 | 5 | 0 |

> **Faustregel**: LMStudio auf Laptop = 1 paralleler Planning-Slot. Jeder weitere Slot erhoehe den RAM-Druck und verlangere alle Runs.

---

## Diagnose-Befehle

```bash
# Slot-Status, stale Goals, Circuit Breaker, Rate-Limit auf einen Blick:
ananta goal planning-stuck

# Mit JSON-Ausgabe fuer Monitoring/Scripting:
ananta goal planning-stuck --json

# Direkt via HTTP:
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:5000/goals/planning/health | python3 -m json.tool
```

---

## Troubleshooting-Entscheidungsbaum

```
Neues Goal bleibt in planning_queued haengen
│
├─ ananta goal planning-stuck zeigt stale_expired_lease > 0?
│  └─ JA → ananta goal recover-stale --yes
│
├─ Slots alle belegt (in_use == capacity)?
│  ├─ JA, Goals laufen aktiv (running > 0, Lease frisch)?
│  │  └─ Warten. Moeglicherweise ist capacity zu niedrig → config erhoehen.
│  │     config: planning_policy.parallel_goal_planning_max_concurrency
│  │
│  └─ JA, aber Goals haengen (running > 0, lease abgelaufen)?
│     └─ ananta goal recover-stale --yes
│
├─ Circuit Breaker state=open?
│  └─ LMStudio/Ollama prueft ob Provider erreichbar.
│     Warten bis circuit_breaker_open_seconds abgelaufen (Recovery automatisch).
│     Oder: Hub neu starten um CB-State zurueckzusetzen.
│
└─ rate_limit: requests_in_last_60s nahe limit_rpm?
   └─ Requests werden temporaer gedrosselt. Kurz warten oder limit_rpm erhoehen.
```

---

## Recovery-Schritte

### Stale planning_running Goal manuell bereinigen

```bash
# 1. Pruefen welche Goals feststecken:
ananta goal planning-stuck

# 2. Automatische Recovery (alle stale Goals mit abgelaufener Lease):
ananta goal recover-stale --yes

# 3. Einzelnes Goal gezielt abbrechen (z.B. bei bekannter ID):
ananta goal cancel-tree <goal-id> --yes
```

### Circuit Breaker manuell zuruecksetzen

Circuit Breaker setzt sich automatisch nach `circuit_breaker_open_seconds` (Default: 60s) zurueck.

Falls sofortiger Reset noetig: Hub neu starten oder warten.

```bash
# Aktuellen CB-Status pruefen:
ananta goal planning-stuck --json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['circuit_breaker'])"
```

### Slot komplett blockiert — Notfall-Purge

```bash
# Alle Tasks eines feststeckenden Goals loeschen und Goal als failed markieren:
ananta goal cancel-tree <goal-id> --yes

# Alternativ via HTTP (Admin):
curl -X DELETE -H "Authorization: Bearer $TOKEN" http://localhost:5000/goals/<goal-id>/purge
```

---

## Konfigurationsreferenz

Alle Felder unter `planning_policy` im Config-Profil:

| Feld | Typ | Default | Bedeutung |
|------|-----|---------|-----------|
| `parallel_goal_planning_max_concurrency` | int | 1 | Max. gleichzeitige Planning-Runs |
| `queue_wait_timeout_seconds` | int | `planning_timeout_s` | Max. Wartezeit im Slot-Queue |
| `planning_timeout_s` | int | 300 | Max. Ausfuehrungs-Timeout fuer Planning-Run |

Unter `llm_config`:

| Feld | Typ | Default | Bedeutung |
|------|-----|---------|-----------|
| `circuit_breaker_threshold` | int | 5 | Fehler bis Circuit oeffnet |
| `circuit_breaker_open_seconds` | int | 60 | Dauer bis Half-Open-Test |
| `rate_limit_rpm` | int | 0 | Max. LLM-Requests/Minute (0=deaktiviert) |
| `rate_limit_rpm_<provider>` | int | `rate_limit_rpm` | Provider-spezifisches Limit |

---

## Do / Don't fuer autonome Testlaeufe

| Do | Don't |
|----|-------|
| `parallel_goal_planning_max_concurrency: 1` fuer LMStudio-Laptop | Mehrere Goals parallel ohne Slot-Limit starten |
| `queue_wait_timeout_seconds: 120` setzen | `queue_wait_timeout_seconds` hoeher als Planning-Timeout setzen |
| Nach autonomem Lauf: `ananta goal planning-stuck` pruefen | Ohne Diagnose-Check neu starten wenn Goals haengen |
| `strategy_mode: autopilot_no_human_review` fuer vollautomatische Laeufe | `waiting_for_review` in automatischen E2E-Laeufen tolerieren |
| Circuit-Breaker-Threshold konservativ (3-5 fuer Laptop) | CB-Threshold auf 0 oder sehr hoch setzen |

---

## Telemetrie-Events

Relevante Log-Eintraege fuer Monitoring:

| Event-String | Bedeutung |
|---|---|
| `planning_preflight_stale_cancelled count=N` | N stale Goals wurden beim Preflight bereinigt |
| `circuit_breaker_open provider=lmstudio failures=N` | CB geoeffnet |
| `circuit_breaker provider=lmstudio state=half_open` | CB testet Recovery |
| `rate_limit_exceeded provider=lmstudio` | Request wegen Rate-Limit abgelehnt |
| `worker_cancel_failed_all_attempts url=...` | Worker nicht erreichbar nach allen Retry-Versuchen |
| `goal_purge_worker_cancel_failed goal_id=...` | Worker-Cancel-Forward fehlgeschlagen |

---

## Verwandte Dokumente

- `docs/cli/commands.md` — CLI-Referenz
- `agent/planning_reason_codes.py` — alle Reason-Codes
- `agent/routes/tasks/goals.py` — Planning-Route, Health-Endpoint
- `todos/todo.parallel-goal-stale-recovery-lmstudio-guard.json` — Implementierungsstand
