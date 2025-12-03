# AI-Agent

Python-basierter Agent, der den Controller pollt, Aufgaben verarbeitet und Logs in die Datenbank schreibt. Implementiert LLM-Aufrufe (Ollama, LM Studio, OpenAI) und optionalen Terminal‑Control‑Modus.

## Laufmodi und Ablauf

Es gibt zwei primäre Pfade, die je nach Einsatz genutzt werden:

1) Task-Polling (Standard; für E2E‑Tests und Queue‑Verarbeitung)
- Der Agent ruft periodisch `GET /tasks/next?agent=<name>` am Controller auf.
- Bei aktivem Enhanced‑Modus liefert der Controller zusätzlich `id` zurück; der Agent kann danach `POST /tasks/<id>/status` mit `done`/`failed` senden.
- Der Agent schreibt Ereignisse in `agent.logs` und in einen kleinen In‑Memory‑Puffer für E2E‑Tests.

2) Terminal‑Control‑Modus
- Der Agent lädt Konfiguration via `GET /next-config` (Modelle, Templates, Agent‑Einstellungen).
- Er erzeugt aus einem Prompt einen Shell‑Befehl, lässt ihn via `POST /approve` bestätigen und führt ihn aus.
- Ein-/Ausgaben und Ergebnisse werden in `data/terminal_log.json` sowie `agent.logs` dokumentiert.

Alle HTTP‑Aufrufe nutzen Timeouts; zentrale Defaults und ENV werden über `src/config/settings.load_settings()` geladen.

## HTTP‑Routen des Agenten (lokaler Flask‑Server des Agents)

| Pfad                 | Methode | Beschreibung |
|----------------------|---------|--------------|
| `/health`            | GET     | Gesundheitscheck des Agent‑Services |
| `/agent/<name>/log`  | GET     | Plain‑Text‑Logpuffer (In‑Memory) für E2E‑Tests |
| `/logs`              | GET     | Aggregierte Logs aus `agent.logs` in der DB |
| `/tasks`             | GET     | Aktueller `current_task` und Taskliste (DB‑Sicht) |
| `/db/contents`       | GET     | Tabellen und Zeilen aus dem Schema `agent` (Pagination) |
| `/stop`              | POST    | Setzt Stop‑Flag in `agent.flags` |
| `/restart`           | POST    | Entfernt Stop‑Flag |

Hinweis: Der Controller bietet eigene, DB‑gestützte Routen mit ähnlichen Pfaden an (z. B. `/agent/<name>/log`), die im Dashboard verwendet werden. Der Agent‑Endpunkt liefert Plain‑Text nur für Tests.

## Konfiguration

Zentrale Einstellungen kommen aus `src/config/settings.py` (ENV, env.json, Defaults). Wichtige Variablen:

| Variable               | Beispielwert                                 | Bedeutung |
|------------------------|----------------------------------------------|-----------|
| `DATABASE_URL`         | `postgresql://user:pass@host:5432/ananta`    | PostgreSQL Verbindung |
| `CONTROLLER_URL`       | `http://controller:8081`                     | Basis‑URL des Controllers |
| `AGENT_NAME`           | `Architect`                                  | Logischer Agent‑Name |
| `AGENT_STARTUP_DELAY`  | `1`                                          | Verzögerung vor dem ersten Poll (Sekunden) |

## Entwicklung & Start

Lokaler Start der Flask‑App (Entwicklungszwecke):

```bash
python -m agent.ai_agent  # startet Flask‑App und/oder Main‑Loop je nach Einstiegspunkt
```

Der Docker‑Compose‑Start wird im Projekt‑README beschrieben.

## Tests

- Python: `pytest`
- E2E (Playwright im Frontend): `npm test` im Verzeichnis `frontend/`

## Hinweise

- Der In‑Memory‑Logpuffer ist auf ~1000 Zeilen pro Agent begrenzt und nur für Tests gedacht; persistente Logs stehen in der Tabelle `agent.logs` zur Verfügung.
- Der Endpunkt `/db/contents` limitiert die Ausgabe per `limit`/`offset` und akzeptiert optional `table` und `include_empty`.
