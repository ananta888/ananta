# AI-Agent

Dieses Verzeichnis enthält den Python-basierten Agenten, der periodisch den Controller abfragt und Aufgaben ausführt.

## Architektur

- `ai_agent.py` implementiert Hilfsfunktionen (`_http_get`, `_http_post`) mit Retry.
- `run_agent()` bildet die Hauptschleife:
  - fragt den Controller über `GET /next-config` nach Konfiguration und Aufgaben,
  - erzeugt über `PromptTemplates` Prompts für den gewünschten LLM,
  - nutzt `ModelPool`, um parallele Anfragen pro Provider zu begrenzen,
  - protokolliert zeitgestempelte Ausgaben im Format `<YYYY-MM-DD HH:MM:SS> LEVEL Nachricht` in `ai_log_<agent>.json` und Zusammenfassungen in `summary_<agent>.txt` im Datenverzeichnis,
  - stoppt sauber, sobald eine `stop.flag`-Datei existiert.

## API-Endpunkte

Der Agent selbst stellt keine eigenen HTTP-Routen bereit, sondern konsumiert folgende Schnittstellen des Controllers bzw. der LLM-Provider:

| Endpoint | Methode | Zweck |
|---------|--------|-------|
| `/next-config` | GET | Nächste Agenten-Konfiguration inkl. Aufgaben und Templates abrufen. |
| `/approve` | POST | Genehmigte Shell-Kommandos und Zusammenfassungen an den Controller melden. |
| `<LLM-URL>` | POST | Aufruf des konfigurierten LLM-Providers (z. B. Ollama, LM Studio, OpenAI). |
