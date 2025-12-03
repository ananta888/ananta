# AI-Agent (Terminal‑Control)

Python-basierter Agent, der ein Terminal über LLM‑generierte Shell‑Befehle steuert und mit einem Controller interagiert.

Es gibt nur noch einen Betriebsmodus: den Terminal‑Control‑Modus.

## Ablauf

1. `GET /next-config` am Controller laden (z. B. Modell, Prompt, Limits).
2. Prompt an den konfigurierten LLM senden → Befehlsvorschlag erhalten.
3. Befehl über `POST /approve` am Controller genehmigen lassen (ggf. Override/Skip).
4. Genehmigten Befehl im Terminal ausführen.
5. Ein-/Ausgaben und Ergebnisse loggen (Datenbank + Datei `data/terminal_log.json`).

Alle HTTP‑Aufrufe nutzen Timeouts; zentrale Defaults und ENV werden über `src/config/settings.load_settings()` geladen.

## HTTP‑Routen des Agenten

Der Agent bringt optional einen minimalen HTTP‑Health‑Endpunkt mit, der beim Start des Terminal‑Control‑Modus automatisch mitgestartet wird:

- `GET /health` → `{ "status": "ok" }`

Weitere, frühere Endpunkte wie `/logs`, `/tasks`, `/db/contents`, `/stop`, `/restart` oder ein In‑Memory‑Logpuffer existieren nicht mehr in diesem Modul.

## Start

Lokal oder in Docker wird der Terminal‑Control‑Modus direkt gestartet:

```bash
python -m agent.ai_agent
```

Der Health‑Endpunkt lauscht standardmäßig auf Port `5000` (konfigurierbar), der Agent kommuniziert mit dem Controller gemäß den Einstellungen.

## Relevante Konfiguration

Konfiguration kommt aus `src/config/settings.py` (ENV, `config.json`, Defaults). Wichtige Variablen:

- `CONTROLLER_URL` (z. B. `http://controller:8081`)
- `OLLAMA_URL`, `LMSTUDIO_URL`, `OPENAI_URL` (LLM‑Endpoints)
- `OPENAI_API_KEY` (falls OpenAI genutzt wird)
- `AGENT_NAME` (logischer Name des Agents)
- `HTTP_TIMEOUT_GET` / `HTTP_TIMEOUT_POST`
- `PORT` (für den optionalen Health‑Endpunkt; Default 5000)

Ehemalige reine Polling‑Einstellungen (z. B. Start‑Delays nur für `/tasks/next`) werden nicht mehr verwendet.

## Tests

- Python: `pytest`
- E2E (Playwright im Frontend): `npm test` im Verzeichnis `frontend/`

## Hinweise

- Das Log des Terminal‑Control‑Ablaufs wird zusätzlich in die DB‑Tabelle `agent.logs` geschrieben.
