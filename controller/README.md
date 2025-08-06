# Controller

Dieses Verzeichnis bündelt den Flask-basierten Controller.

## Architektur

- `controller.py` startet einen Flask-Server, lädt `config.json` aus dem Datenverzeichnis und registriert das Blueprint `src/controller/routes.py`.
- Der Controller verwaltet Aufgaben, Agenten, Blacklist und bietet Log- sowie Exportfunktionen.
- `DashboardManager` rendert das HTML-Dashboard; ein gebautes Vue-Frontend wird aus `/ui` ausgeliefert.
- Standardpfade für Daten (`config.json`, `control_log.json`, `blacklist.txt`) können über die Umgebungsvariable `DATA_DIR` angepasst werden.

## API-Endpunkte

### Kernrouten (`controller/controller.py`)

| Endpoint | Methode | Beschreibung |
|----------|--------|--------------|
| `/next-config` | GET | Liefert die aktive Agentenkonfiguration samt Aufgaben. |
| `/config` | GET | Gibt die komplette Controller-Konfiguration zurück. |
| `/approve` | POST | Übermittelt freigegebene Befehle; Blacklist wird geprüft. |
| `/issues` | GET | Holt GitHub-Issues und reiht sie optional als Aufgaben ein. |
| `/set_theme` | POST | Speichert das gewählte Dashboard-Theme im Cookie. |
| `/` | GET/POST | HTML-Dashboard; POST verarbeitet Formaktionen wie Pipeline- oder Task-Updates. |
| `/agent/<name>/toggle_active` | POST | Schaltet den `controller_active`-Status eines Agents um. |
| `/agent/<name>/log` | GET | Liefert zeitgestempelte Logeinträge eines Agents aus dem Datenverzeichnis. |
| `/stop` | POST | Legt `stop.flag` an und stoppt laufende Agenten. |
| `/restart` | POST | Entfernt `stop.flag` zum Neustart. |
| `/export` | GET | Download von Logs und Konfigurationen als ZIP. |
| `/ui` / `/ui/<pfad>` | GET | Serviert das gebaute Vue-Frontend. |
| `/llm_status` | GET | Prüft konfigurierte LLM-Endpunkte per HEAD-Anfrage. |

### Blueprint-Routen (`src/controller/routes.py`)

| Endpoint | Methode | Beschreibung |
|----------|--------|--------------|
| `/controller/next-task` | GET | Nächste nicht geblockte Aufgabe des Controller-Agenten. |
| `/controller/blacklist` | GET/POST | Listet oder ergänzt Einträge der Blacklist. |
| `/controller/status` | GET | Gibt den internen Log-Status des Controller-Agenten zurück. |
