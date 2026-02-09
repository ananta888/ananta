### Ananta Angular SPA

Lokale Entwicklung

```
cd frontend-angular
npm install
npm start
# App läuft standardmäßig auf http://localhost:4200
```

### Umgebungsvariablen

Das Frontend kann über die folgenden Variablen in der `docker-compose.yml` oder einer `.env`-Datei konfiguriert werden:

| Variable | Beschreibung | Standardwert |
| :--- | :--- | :--- |
| `FRONTEND_PORT` | Der Port, auf dem das Angular Dashboard erreichbar ist. | `4200` |
| `RUN_LIVE_LLM_TESTS` | Schaltet Live-LLM Tests für E2E-Checks frei (`1` = an). | `0` |

### Docker (empfohlen mit Agents)

```
docker-compose up -d
# Frontend: http://localhost:4200
# Hub-Agent (ROLE=hub): http://localhost:5000
# Worker-Agenten:       http://localhost:5001, http://localhost:5002
```

Features (Minimal, lauffähig)
- Agents: Liste/CRUD, Health‑Check, Zugang zum Panel
- Agent Panel: Prompt → Vorschlag → Ausführen, Logs
- Templates (Hub): Anlegen/Löschen, Auflistung (Admin‑Only)
- Teams & Rollen (Hub): Team-/Rollenverwaltung, Team‑Typen (Admin‑Only)
- Board (Hub): Backlog/To‑Do/In‑Progress/Done, Task‑Links
- Task‑Detail (Hub): Status, Zuweisung zu Worker, Propose/Execute, Logs

Konfiguration
- Agent‑Verzeichnis wird in `localStorage` abgelegt (Standardwerte passen zur `docker-compose.yml`).
- Schreibende Endpunkte nutzen einen Bearer‑Token (`Authorization: Bearer <token>`).
- Für den Hub wird bevorzugt ein User‑JWT aus dem Login verwendet.

API‑Beispiele (UI → Hub)

```
POST /tasks                 # Task erstellen
PATCH /tasks/{id}           # Task aktualisieren
POST /tasks/{id}/assign     # Worker zuweisen
POST /tasks/{id}/step/propose
POST /tasks/{id}/step/execute
GET  /tasks/{id}/logs
```

Logs: SSE vs Polling
- SSE ist unter `/tasks/{id}/stream-logs` verfügbar.
- Fallback ist Polling über `/tasks/{id}/logs`.
- Die UI nutzt Polling, wenn SSE nicht verfügbar ist.

Accessibility & UI‑Guidelines
- Kontrast prüfen (WCAG AA) bei neuen Komponenten.
- Fokus‑States bei Buttons/Inputs beibehalten.
- Tastatur‑Navigation für Formulare sicherstellen.
- Theme‑Switching ist derzeit nicht implementiert; neue Komponenten sollen die bestehende Farbpalette verwenden.

Hinweis
- Live‑Streaming der Logs (SSE) ist optional; die UI unterstützt Polling als Fallback.
