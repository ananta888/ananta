# AI-Agent

Der Python-basierte Agent pollt den Controller periodisch und führt erhaltene Aufgaben aus.

## Ablauf

 **Polling** – Der Agent ruft in einem konfigurierbaren Intervall `GET /next-config` beim Controller auf (alternativ kann `/controller/next-task` genutzt werden).
2. **Ausführung** – Für den erhaltenen Task wird über `PromptTemplates` ein Prompt erstellt und an den LLM-Endpunkt gesendet.
3. **Rückmeldung** – Ergebnisse werden mit `POST /approve` an den Controller gesendet.
4. **Retry/Timeout** – HTTP-Aufrufe nutzen das gemeinsame Modul `common/http_client.py` mit Retry- und Timeout-Mechanismen.
5. **Stop-/Restart-Flag** – Über die Agenten-Endpunkte `/stop` und `/restart` kann der Agent angehalten bzw. wieder gestartet werden.

## HTTP-Routen des Agenten

| Pfad      | Methode | Beschreibung                                   |
|-----------|---------|------------------------------------------------|
| `/health` | GET     | einfacher Gesundheitscheck                     |
| `/logs`   | GET     | liefert protokollierte Einträge des Agents     |
| `/tasks`  | GET     | listet aktuelle und ausstehende Tasks          |
| `/stop`   | POST    | setzt ein Stop-Flag in der Datenbank           |
| `/restart`| POST    | entfernt das Stop-Flag                         |

## Aufgabenhistorie

Für jede Agentenrolle führt der AI-Agent eine Datei unter `tasks_history/<rolle>.json`.
Die Datei besteht aus einem JSON-Array, dessen Einträge jeweils `task` und `date` enthalten.
Bei neuen Aufgaben fügt der Agent einen Eintrag mit aktuellem Zeitstempel hinzu.

## Umgebungsvariablen

Alle Werte sind Platzhalter und müssen an die eigene Umgebung angepasst werden:

| Variable          | Beispielwert                                 | Bedeutung                         |
|-------------------|----------------------------------------------|-----------------------------------|
| `DATABASE_URL`    | `postgresql://user:pass@host:5432/ananta`    | Verbindung zur PostgreSQL-Datenbank |
| `AI_AGENT_LOG_LEVEL` | `INFO`                                    | Log-Level des Agenten             |
| `CONTROLLER_URL`  | `http://controller:8081`                     | Basis-URL des Controllers         |

## Tests

Die Agentenfunktionen werden mit `python -m unittest` getestet. Die zentralen Routen sind dabei über einen Flask-Test-Client abgedeckt.
