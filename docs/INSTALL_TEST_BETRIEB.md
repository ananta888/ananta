# Installations-, Test- und Betriebsanleitung

Dieses Dokument beschreibt die Schritte zur Installation, zum Testen und zum Betrieb des Ananta Multi-Agent-Systems.

## 1. Installation

### Voraussetzungen
- **Docker & Docker Compose**: Empfohlen für den schnellsten Start.
- **Python 3.11+**: Für die manuelle Installation des Agents.
- **Node.js 18+ & npm**: Für die manuelle Installation des Frontends.

### A. Schnellstart mit Docker (Empfohlen)
1. Repository klonen oder Dateien kopieren.
2. Im Hauptverzeichnis ausführen:
   ```bash
   docker-compose up -d
   ```
3. Das System ist nun unter folgenden Adressen erreichbar:
   - Frontend: `http://localhost:4200`
   - Hub-Agent: `http://localhost:5000`
   - Worker-Agenten: `http://localhost:5001`, `http://localhost:5002`

### B. Manuelle Installation (Entwicklung)

#### AI-Agent (Hub oder Worker)
1. In das Verzeichnis `agent/` wechseln.
2. Abhängigkeiten installieren: `pip install -r ../requirements.txt`.
3. Starten:
   ```bash
   # Als Hub (Standard-Port 5000)
   ROLE=hub python -m agent.ai_agent
   
   # Als Worker (z.B. Port 5001)
   PORT=5001 python -m agent.ai_agent
   ```

#### Frontend
1. In das Verzeichnis `frontend-angular/` wechseln.
2. Abhängigkeiten installieren: `npm install`.
3. Starten: `npm start`.
4. Browser öffnen: `http://localhost:4200`.

---

## 2. Testbetrieb

Das System verfügt über automatisierte Tests auf verschiedenen Ebenen.

### Unit- & Integrationstests (Backend)
Die Python-Tests decken die API-Endpunkte, Task-Management und Shell-Validierung ab.
```bash
python -m unittest discover tests
```

### E2E-Tests (Frontend)
Die Playwright-Tests prüfen den gesamten Flow im Dashboard.
```bash
cd frontend-angular
npm test
```

### Smoke-Test (Gesamtsystem)
Ein dediziertes Skript prüft, ob der Agent bereit ist und Befehle ausführen kann.
```bash
# Erfordert laufendes System (z.B. via Docker)
python docker/smoke-test.py
```

---

## 3. Betrieb

### Überwachung (Monitoring)
- **Health-Check**: Jeder Agent bietet einen `/health` Endpunkt.
- **Ready-Check**: `/ready` gibt Aufschluss über die Betriebsbereitschaft.
- **Logs**: Terminal-Ausgaben werden in `data/terminal_log.jsonl` persistiert und können über das Frontend oder die API (`/logs`) eingesehen werden.

### Datensicherung (Backup)
Alle relevanten Daten liegen im Verzeichnis `data/`. Zur Sicherung genügt ein Backup dieses Ordners:
- `tasks.json`: Aktuelle Aufgaben und deren Status.
- `templates.json`: Definierte Prompt-Vorlagen.
- `config.json`: Agent-Konfigurationen.

### Skalierung
Weitere Worker-Agenten können einfach hinzugefügt werden, indem neue Instanzen des Agents auf anderen Ports gestartet und im Hub/Frontend registriert werden.

### Sicherheit
- **Tokens**: Nutzen Sie die Umgebungsvariable `AGENT_TOKEN`, um schreibende Zugriffe abzusichern.
- **Shell-Validierung**: Der Agent verfügt über eine Blacklist für gefährliche Befehle (konfigurierbar in `blacklist.txt`).
