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
| `RUN_LIVE_LLM_TESTS` | Schaltet Live-LLM Tests für E2E-Checks frei (`1` = an). Diese Tests erfordern ein laufendes LMStudio-Backend (oder Mock) und werden in der Standard-CI übersprungen, sofern nicht manuell ausgelöst. | `0` |

### E2E-Tests

Das Projekt verwendet Playwright für End-to-End-Tests.

```bash
# Standard-Tests ausführen
npm run test:e2e

# Live-LLM Tests ausführen (erfordert lokales LMStudio auf Port 1234 oder entsprechenden Mock)
npm run test:e2e:live
```

Die Live-LLM Tests (`templates-ai-live.spec.ts`) nutzen `@requires-llm` und werden standardmäßig übersprungen, um CI-Ressourcen zu schonen und Abhängigkeiten zu minimieren. Ein dedizierter CI-Job `frontend-live-llm-tests` steht für manuelle Ausführung oder geplante Läufe zur Verfügung.

### Docker (empfohlen mit Agents)

```
docker-compose up -d
# Frontend: http://localhost:4200
# Hub-Agent (ROLE=hub): http://localhost:5000
# Worker-Agenten:       http://localhost:5001, http://localhost:5002
```

Features (Minimal, lauffähig)
- Agents: Liste/CRUD, Health-Check, Zugang zum Panel
- Agent Panel: Prompt → Vorschlag → Ausführen, Logs
- Templates (Hub): Anlegen/Löschen, Auflistung (Admin-Only)
- Teams & Rollen (Hub): Team-/Rollenverwaltung, Team-Typen (Admin-Only)
- Board (Hub): Backlog/To-Do/In-Progress/Done, Task-Links
- Task-Detail (Hub): Status, Zuweisung zu Worker, Propose/Execute, Logs

Konfiguration
- Agent-Verzeichnis wird in `localStorage` abgelegt (Standardwerte passen zur `docker-compose.yml`).
- Schreibende Endpunkte nutzen einen Bearer-Token (`Authorization: Bearer <token>`).
- Für den Hub wird bevorzugt ein User-JWT aus dem Login verwendet.

API-Beispiele (UI → Hub)

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

### Theme-Switching & UI-State

Die App unterstützt Theme-Switching (Light/Dark).

- **Implementation**: Das Theme wird über die Klasse `.dark` am `<html>`-Element gesteuert.
- **State-Management**: Die Auswahl wird im `ThemeService` verwaltet und in `localStorage` persistiert.
- **Signals**: Die App nutzt Angular Signals für reaktive State-Updates (z.B. User-Profil, Task-Status).

### Accessibility & UI-Guidelines
- Kontrast (WCAG 2.1 AA): Text-zu-Hintergrund mindestens 4.5:1, große Texte 3:1. Dark/Light beachten.
- Fokus-States: Nicht entfernen; sichtbarer Fokus für Buttons, Links und Form-Controls. Nutzung von `:focus-visible` empfohlen.
- Tastatur-Navigation: Alle interaktiven Elemente mit Tab erreichbar, Aktivierung per Enter/Space; kein `tabindex` > 0.
- Semantik: Nutze native Elemente (`<button>`, `<label for>`, Form-Controls) oder setze korrekte ARIA-Rollen/Attribute (sparsam!).
- Live-Regionen: Für asynchrone Statusmeldungen `aria-live="polite"` einsetzen.
- Fehler-Handling: Fehlermeldungen programmatisch zuordenbar (z. B. `aria-describedby`), klare Texte.
- Farbe ist nicht alleinige Trägerin von Information (zusätzliche Icons/Text nutzen).
- Medien: Bilder mit sinnvollen `alt`-Texten; dekorative Bilder mit `alt=""`.
- Responsive Zoom: Keine Viewport-Beschränkungen; Zoom bis 200% ohne Funktionsverlust.
- Performance: LCP/TBT im Blick behalten, da sie auch die Nutzbarkeit mit AT/Keyboard beeinflussen.
- Theme-Switching: Implementiert; neue Komponenten sollen die bestehende Farbpalette nutzen und ausreichende Kontraste in beiden Modi sicherstellen.

A11y- und Audit-Checks (Lighthouse/axe)
- Manuell in Chrome: DevTools → Lighthouse → Kategorien „Accessibility“ und „Best Practices“ auswählen.
- CI/Headless: `npm run audit:a11y` (siehe unten) nutzt Playwright + axe-core für einen schnellen Smoke-Test der Login-/Dashboard-Seiten.
- Lokale Ausführung:
  ```bash
  # SPA starten
  npm start
  # Playwright A11y-Smoke
  npm run test:e2e:a11y
  ```

NPM-Skripte
- `test:e2e:a11y`: Führt axe-core Smoke-Checks gegen zentrale Seiten aus (Login, Dashboard). Ergebnisse im Terminal/HTML-Report.
- `audit:lighthouse`: Optionales Script (falls konfiguriert) für Lighthouse CI oder lokal via `lighthouse http://localhost:4200 --only-categories=accessibility`.

Hinweis
- Live-Streaming der Logs (SSE) ist optional; die UI unterstützt Polling als Fallback.
