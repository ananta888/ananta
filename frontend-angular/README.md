### Ananta Angular SPA

Lokale Entwicklung

```
cd frontend-angular
npm install
npm start
# App läuft auf http://localhost:4200
```

Docker (empfohlen mit Agents)

```
docker-compose up -d
# Frontend: http://localhost:4200
# Hub-Agent (ROLE=hub): http://localhost:5000
# Worker-Agenten:       http://localhost:5001, http://localhost:5002
```

Features (Minimal, lauffähig)
- Agents: Liste/CRUD, Health‑Check, Zugang zum Panel
- Agent Panel: Prompt → Vorschlag → Ausführen, Logs
- Templates (Hub): Anlegen/Löschen, Auflistung
- Board (Hub): Backlog/To‑Do/In‑Progress/Done, Task‑Links
- Task‑Detail (Hub): Status, Zuweisung zu Worker, Propose/Execute, Logs

Konfiguration
- Agent‑Verzeichnis wird in `localStorage` abgelegt (Standardwerte passen zur `docker-compose.yml`).
- Schreibende Endpunkte nutzen optional einen Bearer‑Token je Agent (`Authorization: Bearer <token>`).

Hinweis
- Live‑Streaming der Logs (SSE) ist vorbereitet, derzeit werden Logs gepollt.
